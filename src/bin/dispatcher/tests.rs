use super::*;
use axum::{
    Json, Router,
    extract::State,
    http::{HeaderMap, StatusCode},
    routing::{get, post},
};
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::sync::Notify;

struct Upstream {
    service_id: Mutex<String>,
    limit: AtomicUsize,
    revision: AtomicUsize,
    calls: Mutex<Vec<String>>,
    responses: Mutex<Vec<u16>>,
    started: Notify,
    releases: Semaphore,
}
struct Fixture {
    upstream: Arc<Upstream>,
    dispatcher: Arc<Dispatcher>,
    server: tokio::task::JoinHandle<()>,
    executor: tokio::task::JoinHandle<()>,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.abort();
        self.executor.abort();
    }
}
impl Fixture {
    async fn new(capacity: usize) -> Self {
        let upstream = Arc::new(Upstream {
            service_id: Mutex::new("fixture".into()),
            limit: AtomicUsize::new(5),
            revision: AtomicUsize::new(1),
            calls: Mutex::new(Vec::new()),
            responses: Mutex::new(Vec::new()),
            started: Notify::new(),
            releases: Semaphore::new(0),
        });
        let router = Router::new().route("/agent-policy", get(|State(s): State<Arc<Upstream>>, headers: HeaderMap| async move {
            let other = headers["authorization"] == "Bearer other";
            Json(json!({"version":3,"service_id":s.service_id.lock().unwrap().clone(),"principal_id":if other {"other-owner"} else {"demo-owner"},"client_class":"agent",
                "policy_revision":s.revision.load(Ordering::SeqCst),"refresh_after_ms":100,"outstanding":0,
                "limits":{"scope":"principal","max_outstanding":s.limit.load(Ordering::SeqCst)},
                "operations":[{"name":"search_products","method":"POST","path":"/search"},{"name":"get_product","method":"POST","path":"/product"}]}))
        })).route("/{operation}", post(|State(s): State<Arc<Upstream>>, headers: HeaderMap, axum::extract::Path(operation): axum::extract::Path<String>| async move {
            let token = headers["authorization"].to_str().unwrap().to_string();
            s.calls.lock().unwrap().push(token.clone());
            s.started.notify_one();
            if token == "Bearer limited" && operation == "search" { return (StatusCode::FORBIDDEN, HeaderMap::new(), Json(json!({"error":"forbidden"}))); }
            s.releases.acquire().await.unwrap().forget();
            let status = s.responses.lock().unwrap().pop().unwrap_or(200);
            let mut headers = HeaderMap::new(); headers.insert("Retry-After", "1".parse().unwrap());
            (StatusCode::from_u16(status).unwrap(), headers, Json(json!({"ok":true})))
        })).with_state(upstream.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        let trace = Arc::new(Trace {
            file: None,
            start: Instant::now(),
            epoch_ms: unix_ms(),
        });
        let (dispatcher, queue) = Dispatcher::initialize(
            Client::builder().no_proxy().build().unwrap(),
            base,
            vec!["full-a".into(), "full-b".into(), "limited".into()],
            capacity,
            trace,
        )
        .await
        .unwrap();
        let executor = tokio::spawn(dispatcher.clone().execute(queue));
        Self {
            upstream,
            dispatcher,
            server,
            executor,
        }
    }
    fn request(&self, id: &str, token: &str) -> Request {
        Request {
            id: id.into(),
            token: token.into(),
            operation: "search".into(),
            params: json!({"query":"test"}),
            deadline_unix_ms: unix_ms() + 5000.0,
        }
    }
    async fn start(&self, request: &Request) -> (UnixStream, tokio::task::JoinHandle<()>) {
        let (mut client, server) = UnixStream::pair().unwrap();
        let slot = self
            .dispatcher
            .capacity
            .clone()
            .try_acquire_owned()
            .unwrap();
        let handle = tokio::spawn(self.dispatcher.clone().connection(server, slot));
        let mut bytes = serde_json::to_vec(request).unwrap();
        bytes.push(b'\n');
        client.write_all(&bytes).await.unwrap();
        (client, handle)
    }
    async fn calls(&self, count: usize) {
        timeout(Duration::from_secs(3), async {
            while self.upstream.calls.lock().unwrap().len() < count {
                self.upstream.started.notified().await;
            }
        })
        .await
        .unwrap();
    }
    async fn active(&self, count: usize) {
        let mut changed = self.dispatcher.gate.subscribe();
        timeout(Duration::from_secs(3), async {
            while self.dispatcher.gate.snapshot().active != count {
                changed.changed().await.unwrap();
            }
        })
        .await
        .unwrap();
    }
    fn resize(&self, limit: usize, revision: u64) {
        self.dispatcher.gate.apply(serde_json::from_value(json!({"version":3,"principal_id":"demo-owner","client_class":"agent",
            "policy_revision":revision,"refresh_after_ms":100,"outstanding":0,"limits":{"scope":"principal","max_outstanding":limit}})).unwrap()).unwrap();
    }
}
async fn reply(stream: UnixStream) -> Reply {
    let mut bytes = Vec::new();
    timeout(
        Duration::from_secs(6),
        BufReader::new(stream).read_until(b'\n', &mut bytes),
    )
    .await
    .unwrap()
    .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn permissions_use_the_submitting_token_and_owners_cannot_mix() {
    let f = Fixture::new(8).await;
    let (client, _) = f.start(&f.request("restricted", "limited")).await;
    let result = reply(client).await;
    assert!(!result.ok);
    assert_eq!(result.status, Some(403));
    assert_eq!(result.attempts, Some(1));
    assert_eq!(*f.upstream.calls.lock().unwrap(), ["Bearer limited"]);
    f.upstream.releases.add_permits(1);
    let (client, _) = f.start(&f.request("allowed", "full-b")).await;
    assert!(reply(client).await.ok);
    assert_eq!(f.upstream.calls.lock().unwrap()[1], "Bearer full-b");
    let mixed = Dispatcher::initialize(
        f.dispatcher.http.clone(),
        f.dispatcher.base.clone(),
        vec!["full-a".into(), "other".into()],
        8,
        f.dispatcher.trace.clone(),
    )
    .await;
    assert!(mixed.is_err());
    let (client, _) = f.start(&f.request("unknown", "unregistered")).await;
    assert_eq!(reply(client).await.code, "unregistered_credential");
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn one_shared_queue_obeys_shrink_and_preserves_started_attempts() {
    let f = Fixture::new(16).await;
    let mut clients = Vec::new();
    for i in 0..7 {
        let (client, _) = f
            .start(&f.request(&i.to_string(), if i % 2 == 0 { "full-a" } else { "full-b" }))
            .await;
        clients.push(client);
    }
    f.calls(5).await;
    f.active(5).await;
    f.resize(2, 2);
    f.upstream.releases.add_permits(3);
    f.active(2).await;
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 5);
    f.upstream.releases.add_permits(1);
    f.calls(6).await;
    f.active(2).await;
    f.resize(5, 3);
    f.calls(7).await;
    f.upstream.releases.add_permits(3);
    for client in clients {
        assert!(reply(client).await.ok);
    }
    f.active(0).await;
}

#[tokio::test]
async fn disconnect_keeps_running_permit_and_expired_waiter_never_sends() {
    let f = Fixture::new(8).await;
    f.resize(1, 2);
    let (client, handler) = f.start(&f.request("running", "full-a")).await;
    f.calls(1).await;
    drop(client);
    handler.await.unwrap();
    assert_eq!(f.dispatcher.gate.snapshot().active, 1);
    let mut queued = f.request("expired", "full-b");
    queued.deadline_unix_ms = unix_ms() + 500.0;
    let (client, _) = f.start(&queued).await;
    let expired = reply(client).await;
    assert_eq!(expired.code, "task_deadline");
    assert_eq!(expired.execution, "not_started");
    assert_eq!(expired.attempts, Some(0));
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 1);
    f.upstream.releases.add_permits(1);
    f.active(0).await;
    let mut already_expired = f.request("old", "full-b");
    already_expired.deadline_unix_ms = unix_ms() - 10.0;
    let (client, _) = f.start(&already_expired).await;
    assert_eq!(reply(client).await.execution, "not_started");
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn running_deadline_is_unknown_and_does_not_retry_or_release_early() {
    let f = Fixture::new(8).await;
    let mut request = f.request("slow", "full-a");
    request.deadline_unix_ms = unix_ms() + 500.0;
    let (client, _) = f.start(&request).await;
    f.calls(1).await;
    let result = reply(client).await;
    assert_eq!(result.code, "task_deadline");
    assert_eq!(result.execution, "unknown");
    assert_eq!(f.dispatcher.gate.snapshot().active, 1);
    f.upstream.releases.add_permits(1);
    f.active(0).await;
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn retries_release_capacity_and_stop_at_five_on_the_original_deadline() {
    let f = Fixture::new(8).await;
    *f.upstream.responses.lock().unwrap() = vec![429; 5];
    f.upstream.releases.add_permits(5);
    let start = Instant::now();
    let (client, _) = f.start(&f.request("retry", "full-a")).await;
    let result = reply(client).await;
    assert_eq!(result.attempts, Some(5));
    assert_eq!(result.status, Some(429));
    assert_eq!(result.execution, "not_started");
    assert!(start.elapsed() >= Duration::from_secs(4));
    f.active(0).await;
    // Shared retry tests cover exact virtual-clock pauses and unsafe 503/network errors.
}

#[tokio::test]
async fn polling_applies_both_revisions_using_one_owner_gate() {
    let f = Fixture::new(8).await;
    let poller = tokio::spawn(f.dispatcher.clone().poll("limited".into()));
    for (limit, revision) in [(2, 2), (5, 3)] {
        f.upstream.limit.store(limit, Ordering::SeqCst);
        f.upstream.revision.store(revision, Ordering::SeqCst);
        let mut changed = f.dispatcher.gate.subscribe();
        timeout(Duration::from_secs(2), async {
            while f.dispatcher.gate.snapshot().revision != revision as u64 {
                changed.changed().await.unwrap();
            }
        })
        .await
        .unwrap();
        assert_eq!(f.dispatcher.gate.snapshot().maximum, limit);
    }
    poller.abort();
    let _ = poller.await;
}

#[tokio::test]
async fn bounded_queue_and_lost_reply_are_explicit_without_direct_fallback() {
    let f = Fixture::new(1).await;
    let directory = std::env::temp_dir().join(format!("dispatcher-test-{}", std::process::id()));
    std::fs::create_dir(&directory).unwrap();
    let socket = directory.join("socket");
    let listener = UnixListener::bind(&socket).unwrap();
    let server = tokio::spawn(serve(f.dispatcher.clone(), listener));
    let request = f.request("running", "full-a");
    let path = socket.to_str().unwrap().to_string();
    let first = tokio::spawn({
        let path = path.clone();
        async move { dispatch::submit(&path, &request, Instant::now() + Duration::from_secs(5)).await }
    });
    f.calls(1).await;
    let full = dispatch::submit(
        &path,
        &f.request("overflow", "full-b"),
        Instant::now() + Duration::from_secs(2),
    )
    .await;
    assert_eq!(full.code, "dispatcher_queue_full");
    assert_eq!(full.execution, "not_started");
    server.abort();
    let _ = server.await;
    let lost = first.await.unwrap();
    assert_eq!(lost.code, "dispatcher_response_lost");
    assert_eq!(lost.execution, "unknown");
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 1);
    f.upstream.releases.add_permits(1);
    f.active(0).await;
    std::fs::remove_file(&socket).unwrap();
    let unavailable = dispatch::submit(
        &path,
        &f.request("gone", "full-a"),
        Instant::now() + Duration::from_secs(1),
    )
    .await;
    assert_eq!(unavailable.code, "dispatcher_unavailable");
    assert_eq!(f.upstream.calls.lock().unwrap().len(), 1);
    std::fs::remove_dir(directory).unwrap();
}

#[tokio::test(start_paused = true)]
async fn dispatch_and_cancellation_have_one_linearization_point() {
    let waiting = Arc::new(Progress::default());
    let deadline = Instant::now() + Duration::from_secs(1);
    assert_eq!(waiting.failure("task_deadline").execution, "not_started");
    assert!(!waiting.begin(deadline));
    let running = Arc::new(Progress::default());
    assert!(running.begin(deadline));
    assert_eq!(running.failure("task_deadline").execution, "unknown");
    assert!(!running.begin(deadline));
    let expired = Progress::default();
    tokio::time::advance(Duration::from_secs(1)).await;
    assert!(!expired.begin(deadline));
    assert_eq!(expired.failure("task_deadline").attempts, Some(0));
    let cancelled = Arc::new(Progress::default());
    drop(Cancellation(cancelled.clone()));
    assert!(!cancelled.begin(Instant::now() + Duration::from_secs(1)));
}

#[tokio::test]
async fn equal_owner_ids_on_different_services_have_independent_gates_and_queues() {
    let a = Fixture::new(8).await;
    let b = Fixture::new(8).await;
    assert_eq!(
        a.dispatcher.gate.snapshot().principal,
        b.dispatcher.gate.snapshot().principal
    );
    assert_ne!(a.dispatcher.base, b.dispatcher.base);
    a.resize(1, 2);
    b.resize(2, 2);
    let (a1, _) = a.start(&a.request("a1", "full-a")).await;
    a.calls(1).await;
    let (a2, _) = a.start(&a.request("a2", "full-b")).await;
    let mut request = b.request("b1", "full-a");
    request.operation = "search_products".into();
    let (b1, _) = b.start(&request).await;
    let (b2, _) = b.start(&b.request("b2", "full-b")).await;
    b.calls(2).await;
    assert_eq!(a.dispatcher.gate.snapshot().active, 1);
    assert_eq!(b.dispatcher.gate.snapshot().active, 2);
    assert_eq!(a.upstream.calls.lock().unwrap().len(), 1);
    b.upstream.releases.add_permits(2);
    assert!(reply(b1).await.ok && reply(b2).await.ok);
    b.active(0).await;
    assert_eq!(a.dispatcher.gate.snapshot().active, 1);
    a.upstream.releases.add_permits(2);
    assert!(reply(a1).await.ok && reply(a2).await.ok);
    a.active(0).await;
}

#[test]
fn discovery_routes_are_generic_explicit_and_cannot_redirect_credentials() {
    let mut routes = HashMap::new();
    add_routes(
        &mut routes,
        vec![OperationRoute {
            name: "search_tickets".into(),
            method: "POST".into(),
            path: "/tickets/search".into(),
        }],
    )
    .unwrap();
    assert_eq!(routes["search_tickets"], "tickets/search");
    assert_eq!(routes["tickets/search"], "tickets/search");
    assert!(!routes.contains_key("http://evil"));
    for path in [
        "//evil",
        "http://evil",
        "/../admin",
        "/a?url=x",
        "/a#b",
        "/%2f",
    ] {
        assert!(
            add_routes(
                &mut routes,
                vec![OperationRoute {
                    name: "attack".into(),
                    method: "POST".into(),
                    path: path.into()
                }]
            )
            .is_err()
        );
    }
    assert!(
        add_routes(
            &mut routes,
            vec![OperationRoute {
                name: "search_tickets".into(),
                method: "POST".into(),
                path: "/other".into()
            }]
        )
        .is_err()
    );
}

#[tokio::test]
async fn service_identity_change_pauses_the_existing_gate() {
    let f = Fixture::new(8).await;
    *f.upstream.service_id.lock().unwrap() = "replacement".into();
    let mut changed = f.dispatcher.gate.subscribe();
    let poller = tokio::spawn(f.dispatcher.clone().poll("full-a".into()));
    timeout(Duration::from_secs(3), async {
        while f.dispatcher.gate.snapshot().ready {
            changed.changed().await.unwrap();
        }
    })
    .await
    .unwrap();
    assert_eq!(f.dispatcher.gate.snapshot().active, 0);
    assert!(f.upstream.calls.lock().unwrap().is_empty());
    poller.abort();
}
