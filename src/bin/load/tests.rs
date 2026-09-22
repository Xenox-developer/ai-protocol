use super::*;
use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
};
use std::sync::{
    Mutex,
    atomic::{AtomicU16, AtomicUsize, Ordering},
};
use tokio::sync::{Semaphore, mpsc};
use tokio::time::timeout;

fn policy(maximum: usize, revision: u64) -> serde_json::Value {
    serde_json::json!({"version":3, "principal_id":"demo-owner", "client_class":"agent",
        "policy_revision":revision, "refresh_after_ms":1000, "outstanding":0,
        "limits":{"scope":"principal", "max_outstanding":maximum}})
}

fn apply(gate: &Gate, maximum: usize, revision: u64) {
    gate.apply(serde_json::from_value(policy(maximum, revision)).unwrap())
        .unwrap();
}

async fn wait_for(gate: &Gate, predicate: impl Fn(&control::State) -> bool) {
    let mut changed = gate.subscribe();
    timeout(Duration::from_secs(4), async {
        while !predicate(&gate.snapshot()) {
            changed.changed().await.unwrap();
        }
    })
    .await
    .expect("client state did not change");
}

async fn assert_pending<T>(mut future: std::pin::Pin<&mut impl std::future::Future<Output = T>>) {
    std::future::poll_fn(|context| {
        assert!(future.as_mut().poll(context).is_pending());
        std::task::Poll::Ready(())
    })
    .await;
}

#[tokio::test]
async fn limiter_shrinks_without_cancelling_and_wakes_on_growth() {
    let gate = Gate::new();
    apply(&gate, 5, 1);
    let mut held = Vec::new();
    for _ in 0..5 {
        held.push(gate.acquire().await.unwrap());
    }
    apply(&gate, 2, 2);
    assert_eq!(gate.snapshot().active, 5);
    let pending = gate.acquire();
    tokio::pin!(pending);
    assert_pending(pending.as_mut()).await;
    for _ in 0..3 {
        held.pop();
        assert_pending(pending.as_mut()).await;
    }
    held.pop();
    held.push(pending.await.unwrap());
    assert_eq!(gate.snapshot().active, 2);
    let pending = gate.acquire();
    tokio::pin!(pending);
    assert_pending(pending.as_mut()).await;
    apply(&gate, 5, 3);
    held.push(pending.await.unwrap());
    held.push(gate.acquire().await.unwrap());
    held.push(gate.acquire().await.unwrap());
    assert_eq!(gate.snapshot().active, 5);
    drop(held);
    assert_eq!(gate.snapshot().active, 0);
}

#[tokio::test]
async fn paused_and_stopped_clients_do_not_admit_new_attempts() {
    let gate = Gate::new();
    let pending = gate.acquire();
    tokio::pin!(pending);
    assert_pending(pending.as_mut()).await;
    apply(&gate, 5, 1);
    let held = pending.await.unwrap();
    gate.pause();
    let pending = gate.acquire();
    tokio::pin!(pending);
    drop(held);
    assert_pending(pending.as_mut()).await;
    apply(&gate, 2, 2);
    let held = pending.await.unwrap();
    gate.pause();
    let pending = gate.acquire();
    tokio::pin!(pending);
    assert_pending(pending.as_mut()).await;
    gate.stop();
    assert!(pending.await.is_err());
    drop(held);
    assert_eq!(gate.snapshot().active, 0);
}

struct Fixture {
    policy: Mutex<(StatusCode, serde_json::Value)>,
    policy_calls: AtomicUsize,
    started: mpsc::UnboundedSender<()>,
    release: Semaphore,
    operation_status: AtomicU16,
    attempts: AtomicUsize,
}

struct Server {
    client: LoadClient,
    fixture: Arc<Fixture>,
    started: mpsc::UnboundedReceiver<()>,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn server() -> Server {
    let (tx, rx) = mpsc::unbounded_channel();
    let fixture = Arc::new(Fixture {
        policy: Mutex::new((StatusCode::OK, policy(5, 1))),
        policy_calls: AtomicUsize::new(0),
        started: tx,
        release: Semaphore::new(0),
        operation_status: AtomicU16::new(200),
        attempts: AtomicUsize::new(0),
    });
    let app = Router::new()
        .route(
            "/agent-policy",
            get(|State(state): State<Arc<Fixture>>| async move {
                state.policy_calls.fetch_add(1, Ordering::SeqCst);
                let (status, body) = state.policy.lock().unwrap().clone();
                (status, Json(body))
            }),
        )
        .route(
            "/search",
            post(|State(state): State<Arc<Fixture>>| async move {
                state.attempts.fetch_add(1, Ordering::SeqCst);
                state.started.send(()).unwrap();
                state.release.acquire().await.unwrap().forget();
                let status =
                    StatusCode::from_u16(state.operation_status.load(Ordering::SeqCst)).unwrap();
                (
                    status,
                    [("Retry-After", "0")],
                    Json(serde_json::json!({"products":[]})),
                )
                    .into_response()
            }),
        )
        .with_state(fixture.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    Server {
        client: LoadClient {
            http: Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap(),
            base_url: url,
            gate: Gate::new(),
            agent_token: "test-token".into(),
            interactive_token: None,
        },
        fixture,
        started: rx,
        task,
    }
}

impl Server {
    async fn entered(&mut self, count: usize) {
        for _ in 0..count {
            timeout(Duration::from_secs(4), self.started.recv())
                .await
                .unwrap()
                .unwrap();
        }
    }
}

#[tokio::test]
async fn one_poller_adapts_real_http_sending_while_all_operation_slots_are_busy() {
    let mut server = server().await;
    let poller = tokio::spawn(poll_policy(server.client.clone()));
    wait_for(&server.client.gate, |state| state.ready).await;
    let mut tasks = JoinSet::new();
    for _ in 0..5 {
        tasks.spawn(send_request(server.client.clone(), "agent"));
    }
    server.entered(5).await;
    *server.fixture.policy.lock().unwrap() = (StatusCode::OK, policy(2, 2));
    wait_for(&server.client.gate, |state| state.revision == 2).await;
    assert_eq!(server.client.gate.snapshot().active, 5);
    for _ in 0..3 {
        tasks.spawn(send_request(server.client.clone(), "agent"));
    }
    server.fixture.release.add_permits(3);
    for _ in 0..3 {
        tasks.join_next().await.unwrap().unwrap().unwrap();
    }
    assert_eq!(server.client.gate.snapshot().active, 2);
    assert!(server.started.try_recv().is_err());
    server.fixture.release.add_permits(1);
    tasks.join_next().await.unwrap().unwrap().unwrap();
    server.entered(1).await;
    assert_eq!(server.client.gate.snapshot().active, 2);
    *server.fixture.policy.lock().unwrap() = (StatusCode::OK, policy(5, 3));
    wait_for(&server.client.gate, |state| state.revision == 3).await;
    server.entered(2).await;
    tasks.spawn(send_request(server.client.clone(), "agent"));
    server.entered(1).await;
    assert_eq!(server.client.gate.snapshot().active, 5);
    server.fixture.release.add_permits(5);
    while let Some(result) = tasks.join_next().await {
        result.unwrap().unwrap();
    }
    assert_eq!(server.client.gate.snapshot().active, 0);
    assert_eq!(server.fixture.attempts.load(Ordering::SeqCst), 9);
    assert!(server.fixture.policy_calls.load(Ordering::SeqCst) <= 5);
    poller.abort();
}

#[tokio::test]
async fn polling_failure_pauses_new_http_attempts_and_recovers() {
    let mut server = server().await;
    let poller = tokio::spawn(poll_policy(server.client.clone()));
    wait_for(&server.client.gate, |state| state.ready).await;
    let mut tasks = JoinSet::new();
    for _ in 0..2 {
        tasks.spawn(send_request(server.client.clone(), "agent"));
    }
    server.entered(2).await;
    *server.fixture.policy.lock().unwrap() =
        (StatusCode::SERVICE_UNAVAILABLE, serde_json::json!({}));
    wait_for(&server.client.gate, |state| !state.ready).await;
    tasks.spawn(send_request(server.client.clone(), "agent"));
    server.fixture.release.add_permits(2);
    for _ in 0..2 {
        tasks.join_next().await.unwrap().unwrap().unwrap();
    }
    assert_eq!(server.client.gate.snapshot().active, 0);
    assert_eq!(server.fixture.attempts.load(Ordering::SeqCst), 2);
    *server.fixture.policy.lock().unwrap() = (StatusCode::OK, policy(2, 2));
    server.entered(1).await;
    assert!(server.client.gate.snapshot().ready);
    server.fixture.release.add_permits(1);
    tasks.join_next().await.unwrap().unwrap().unwrap();
    assert!(server.fixture.policy_calls.load(Ordering::SeqCst) <= 5);
    poller.abort();
}

#[tokio::test]
async fn invalid_or_inconsistent_policy_pauses_until_valid_snapshot_returns() {
    let server = server().await;
    refresh_policy(&server.client).await;
    let original = policy(5, 1);
    let mut invalid = Vec::new();
    for (key, value) in [
        ("version", serde_json::json!(2)),
        ("policy_revision", serde_json::json!(0)),
        ("refresh_after_ms", serde_json::json!(0)),
        ("principal_id", serde_json::json!("other")),
        ("client_class", serde_json::json!("interactive")),
    ] {
        let mut body = original.clone();
        body[key] = value;
        invalid.push(body);
    }
    invalid.push(policy(2, 1));
    invalid.push(policy(0, 2));
    invalid.push(serde_json::json!({"version":3}));
    for body in invalid {
        *server.fixture.policy.lock().unwrap() = (StatusCode::OK, body);
        assert_eq!(refresh_policy(&server.client).await, Duration::from_secs(1));
        assert!(!server.client.gate.snapshot().ready);
    }
    *server.fixture.policy.lock().unwrap() = (StatusCode::OK, policy(2, 2));
    refresh_policy(&server.client).await;
    assert!(server.client.gate.snapshot().ready);
    // A server restart with a reset revision must not silently roll policy back.
    *server.fixture.policy.lock().unwrap() = (StatusCode::OK, policy(5, 1));
    refresh_policy(&server.client).await;
    assert!(!server.client.gate.snapshot().ready);
}

#[tokio::test]
async fn retries_are_bounded_and_attempt_slots_are_released_on_errors() {
    let server = server().await;
    refresh_policy(&server.client).await;
    server.fixture.operation_status.store(429, Ordering::SeqCst);
    server.fixture.release.add_permits(5);
    assert!(send_request(server.client.clone(), "agent").await.is_err());
    assert_eq!(server.fixture.attempts.load(Ordering::SeqCst), 5);
    assert_eq!(server.client.gate.snapshot().active, 0);
    server.fixture.operation_status.store(502, Ordering::SeqCst);
    server.fixture.release.add_permits(1);
    assert!(send_request(server.client.clone(), "agent").await.is_err());
    assert_eq!(server.fixture.attempts.load(Ordering::SeqCst), 6);
    assert_eq!(server.client.gate.snapshot().active, 0);
}
