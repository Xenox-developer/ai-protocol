use super::*;
use tokio::sync::{Semaphore, mpsc};
use tokio::task::JoinHandle;
use tokio::time::timeout;

fn test_state() -> Arc<AppState> {
    AppState::new(
        identities_from(|name| Ok(format!("test-{name}"))).unwrap(),
        false,
        None,
    )
}

fn identity(state: &AppState, name: &str) -> ClientIdentity {
    state.identities[&format!("test-{name}")].clone()
}

struct Server {
    url: String,
    task: JoinHandle<()>,
}

impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn serve(router: Router) -> Server {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    Server { url, task }
}

fn http() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap()
}

async fn slots(budget: &Budget, expected: usize) {
    timeout(Duration::from_secs(3), async {
        while budget.available_permits() != expected {
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("budget did not reach the expected value");
}

fn submit(state: &Arc<AppState>, name: &str, action: TaskAction) -> JoinHandle<Response> {
    tokio::spawn(enqueue(state.clone(), identity(state, name), action))
}

async fn finish(task: JoinHandle<Response>) -> Response {
    timeout(Duration::from_secs(3), task)
        .await
        .unwrap()
        .unwrap()
}

#[tokio::test]
async fn authentication_permissions_and_discovery() {
    let state = test_state();
    let server = serve(app(state.clone())).await;
    let client = http();
    for path in ["/agent-policy", "/search", "/product"] {
        for token in [None, Some("unknown")] {
            let mut request = if path == "/agent-policy" {
                client.get(format!("{}{path}", server.url))
            } else {
                client
                    .post(format!("{}{path}", server.url))
                    .json(&serde_json::json!({}))
            }
            .header("X-Client-Type", "human");
            if let Some(token) = token {
                request = request.bearer_auth(token);
            }
            let response = request.send().await.unwrap();
            assert_eq!(response.status(), 401);
            assert_eq!(response.headers()["www-authenticate"], "Bearer");
            assert_eq!(response.headers()["cache-control"], "no-store");
        }
    }
    for spoof in [None, Some("human"), Some("interactive")] {
        let mut request = client
            .get(format!("{}/agent-policy", server.url))
            .bearer_auth("test-AGENT_TOKEN_1");
        if let Some(value) = spoof {
            request = request.header("X-Client-Type", value);
        }
        let response = request.send().await.unwrap();
        assert_eq!(response.headers()["cache-control"], "no-store");
        let policy: serde_json::Value = response.json().await.unwrap();
        assert_eq!(policy["version"], 3);
        assert_eq!(policy["service_id"], "catalog");
        assert_eq!(policy["principal_id"], "demo-owner");
        assert_eq!(policy["client_class"], "agent");
        assert_eq!(policy["limits"]["scope"], "principal");
        assert_eq!(policy["limits"]["max_outstanding"], 5);
        assert_eq!(policy["operations"].as_array().unwrap().len(), 2);
    }
    let policy: serde_json::Value = client
        .get(format!("{}/agent-policy", server.url))
        .bearer_auth("test-PRODUCT_ONLY_TOKEN")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(policy["operations"].as_array().unwrap().len(), 1);
    assert_eq!(policy["operations"][0]["name"], "get_product");
    let response = client
        .post(format!("{}/search", server.url))
        .bearer_auth("test-PRODUCT_ONLY_TOKEN")
        .json(&serde_json::json!({"query":""}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 403);
    assert!(state.queues.lock().await.agents.is_empty());
    let response = client
        .get(format!("{}/agent-policy", server.url))
        .header("Authorization", "Bearer test-AGENT_TOKEN_1")
        .header("Authorization", "Bearer test-INTERACTIVE_TOKEN")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
}

#[tokio::test]
async fn spoofed_headers_cannot_select_interactive_queue() {
    let state = test_state();
    let server = serve(app(state.clone())).await;
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let mut requests = Vec::new();
    for spoof in [None, Some("human"), Some("interactive")] {
        let mut request = http()
            .post(format!("{}/search", server.url))
            .bearer_auth("test-AGENT_TOKEN_1")
            .json(&serde_json::json!({"query":""}));
        if let Some(value) = spoof {
            request = request.header("X-Client-Type", value);
        }
        requests.push(tokio::spawn(request.send()));
    }
    slots(&budget, 2).await;
    let mut queues = state.queues.lock().await;
    assert_eq!(queues.agents.len(), 3);
    assert!(queues.interactive.is_empty());
    while let Some(job) = queues.pop_next() {
        job.reply.send(StatusCode::OK.into_response()).unwrap();
    }
    drop(queues);
    for task in requests {
        assert_eq!(task.await.unwrap().unwrap().status(), 200);
    }
    slots(&budget, 5).await;
}

#[tokio::test]
async fn concurrent_tokens_share_budget_and_other_scopes_are_independent() {
    let state = test_state();
    let a = identity(&state, "AGENT_TOKEN_1");
    let b = identity(&state, "AGENT_TOKEN_2");
    assert!(Arc::ptr_eq(&a.budget, &b.budget));
    assert!(Arc::ptr_eq(
        &a.budget,
        &identity(&state, "PRODUCT_ONLY_TOKEN").budget
    ));
    let mut tasks = Vec::new();
    for index in 0..20 {
        tasks.push(submit(
            &state,
            if index % 2 == 0 {
                "AGENT_TOKEN_1"
            } else {
                "AGENT_TOKEN_2"
            },
            TaskAction::Search("".into()),
        ));
    }
    timeout(Duration::from_secs(3), async {
        while tasks.iter().filter(|task| task.is_finished()).count() < 15 {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert_eq!(a.budget.available_permits(), 0);
    assert_eq!(state.queues.lock().await.agents.len(), 5);
    let other = submit(&state, "OTHER_AGENT_TOKEN", TaskAction::GetProduct(2));
    let interactive = submit(&state, "INTERACTIVE_TOKEN", TaskAction::GetProduct(2));
    slots(&identity(&state, "OTHER_AGENT_TOKEN").budget, 4).await;
    slots(&identity(&state, "INTERACTIVE_TOKEN").budget, 9).await;
    let mut queues = state.queues.lock().await;
    // A fresh weighted cycle selects an interactive job first.
    assert_eq!(queues.interactive.len(), 1);
    let job = queues.pop_next().unwrap();
    assert!(matches!(job.action, TaskAction::GetProduct(2)));
    job.reply.send(StatusCode::OK.into_response()).unwrap();
    drop(job.permit);
    while let Some(job) = queues.pop_next() {
        job.reply.send(StatusCode::OK.into_response()).unwrap();
    }
    drop(queues);
    let mut accepted = 0;
    let mut rejected = 0;
    for task in tasks {
        let response = finish(task).await;
        match response.status() {
            StatusCode::OK => accepted += 1,
            StatusCode::TOO_MANY_REQUESTS => {
                rejected += 1;
                assert_eq!(response.headers()["retry-after"], "1");
            }
            status => panic!("Unexpected {status}"),
        }
    }
    assert_eq!((accepted, rejected), (5, 15));
    assert_eq!(finish(other).await.status(), 200);
    assert_eq!(finish(interactive).await.status(), 200);
    slots(&a.budget, 5).await;
}

#[tokio::test]
async fn queued_cancellation_releases_slot_and_never_runs() {
    let state = test_state();
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let task = submit(
        &state,
        "AGENT_TOKEN_1",
        TaskAction::Search("cancelled".into()),
    );
    slots(&budget, 4).await;
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    let replacement = submit(&state, "AGENT_TOKEN_2", TaskAction::GetProduct(2));
    timeout(Duration::from_secs(3), async {
        loop {
            let queues = state.queues.lock().await;
            if queues
                .agents
                .front()
                .is_some_and(|job| matches!(job.action, TaskAction::GetProduct(2)))
            {
                break;
            }
            drop(queues);
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert_eq!(budget.available_permits(), 4);
    replacement.abort();
    assert!(replacement.await.unwrap_err().is_cancelled());
    assert!(state.queues.lock().await.pop_next().is_none());
    slots(&budget, 5).await;
}

struct Upstream {
    server: Server,
    release: Arc<Semaphore>,
    started: mpsc::UnboundedReceiver<()>,
}

async fn upstream(status: StatusCode) -> Upstream {
    let release = Arc::new(Semaphore::new(0));
    let gate = release.clone();
    let (tx, started) = mpsc::unbounded_channel();
    let router = Router::new().fallback(get(move || {
        let gate = gate.clone();
        let tx = tx.clone();
        async move {
            tx.send(()).unwrap();
            gate.acquire().await.unwrap().forget();
            (status, Json(serde_json::json!({"ok":true})))
        }
    }));
    Upstream {
        server: serve(router).await,
        release,
        started,
    }
}

#[tokio::test]
async fn running_cancellation_keeps_permit_until_upstream_finishes() {
    let state = test_state();
    let mut upstream = upstream(StatusCode::OK).await;
    let scheduler = tokio::spawn(scheduler(state.clone(), upstream.server.url.clone()));
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let mut tasks = Vec::new();
    for _ in 0..5 {
        tasks.push(submit(
            &state,
            "AGENT_TOKEN_1",
            TaskAction::Search("".into()),
        ));
    }
    for _ in 0..5 {
        timeout(Duration::from_secs(3), upstream.started.recv())
            .await
            .unwrap()
            .unwrap();
    }
    let cancelled = tasks.pop().unwrap();
    cancelled.abort();
    assert!(cancelled.await.unwrap_err().is_cancelled());
    assert_eq!(budget.available_permits(), 0);
    let response = finish(submit(&state, "AGENT_TOKEN_2", TaskAction::GetProduct(2))).await;
    assert_eq!(response.status(), 429);
    assert!(upstream.started.try_recv().is_err());
    upstream.release.add_permits(5);
    for task in tasks {
        assert_eq!(finish(task).await.status(), 200);
    }
    slots(&budget, 5).await;
    let next = submit(&state, "AGENT_TOKEN_2", TaskAction::GetProduct(2));
    timeout(Duration::from_secs(3), upstream.started.recv())
        .await
        .unwrap()
        .unwrap();
    upstream.release.add_permits(1);
    assert_eq!(finish(next).await.status(), 200);
    slots(&budget, 5).await;
    scheduler.abort();
}

#[tokio::test]
async fn upstream_errors_release_budget_and_429_is_not_forwarded() {
    for status in [
        StatusCode::INTERNAL_SERVER_ERROR,
        StatusCode::TOO_MANY_REQUESTS,
    ] {
        let state = test_state();
        let mut upstream = upstream(status).await;
        let scheduler = tokio::spawn(scheduler(state.clone(), upstream.server.url.clone()));
        let task = submit(&state, "AGENT_TOKEN_1", TaskAction::Search("".into()));
        timeout(Duration::from_secs(3), upstream.started.recv())
            .await
            .unwrap()
            .unwrap();
        upstream.release.add_permits(1);
        let response = finish(task).await;
        assert_eq!(response.status(), 502);
        assert!(response.headers().get("retry-after").is_none());
        slots(&identity(&state, "AGENT_TOKEN_1").budget, 5).await;
        scheduler.abort();
    }
}

#[tokio::test]
async fn queue_overflow_does_not_leak_either_class_budget() {
    for class in [ClientClass::Agent, ClientClass::Interactive] {
        let state = test_state();
        // Independent synthetic principals exercise the global queue cap.
        let identity = ClientIdentity {
            principal_id: "queue-test".into(),
            class,
            operations: vec!["get_product".into()],
            budget: Budget::new(QUEUE_LIMIT + 1),
        };
        let mut tasks = Vec::new();
        for _ in 0..QUEUE_LIMIT {
            tasks.push(tokio::spawn(enqueue(
                state.clone(),
                identity.clone(),
                TaskAction::GetProduct(1),
            )));
        }
        slots(&identity.budget, 1).await;
        let response = enqueue(state.clone(), identity.clone(), TaskAction::GetProduct(1)).await;
        assert_eq!(response.status(), 429);
        assert_eq!(identity.budget.available_permits(), 1);
        for task in tasks {
            task.abort();
            let _ = task.await;
        }
        state.queues.lock().await.prune_cancelled();
        slots(&identity.budget, QUEUE_LIMIT + 1).await;
    }
}

#[test]
fn configuration_fails_closed_without_exposing_tokens() {
    assert!(identities_from(|_| Err(std::env::VarError::NotPresent)).is_err());
    assert!(identities_from(|_| Ok(String::new())).is_err());
    let error = identities_from(|_| Ok("same-secret".into())).err().unwrap();
    assert!(!error.contains("same-secret"));
    assert!(
        identities_from(|name| {
            if [
                "OTHER_AGENT_TOKEN",
                "PRODUCT_ONLY_TOKEN",
                "AGENT_TOKEN_3",
                "AGENT_TOKEN_4",
            ]
            .contains(&name)
            {
                Err(std::env::VarError::NotPresent)
            } else {
                Ok(format!("test-{name}"))
            }
        })
        .is_ok()
    );
}

#[tokio::test]
async fn catalog_operations_work_through_authenticated_http() {
    let catalog = serve(
        Router::new()
            .route(
                "/products/search",
                get(
                    |axum::extract::Query(params): axum::extract::Query<
                        HashMap<String, String>,
                    >| async move {
                        assert_eq!(params["query"], "");
                        Json(serde_json::json!({"products":[{"id":1},{"id":2},{"id":3}]}))
                    },
                ),
            )
            .route(
                "/products/get",
                get(
                    |axum::extract::Query(params): axum::extract::Query<
                        HashMap<String, String>,
                    >| async move {
                        assert_eq!(params["id"], "2");
                        Json(serde_json::json!({"product":{"id":2,"name":"Brown boots"}}))
                    },
                ),
            ),
    )
    .await;
    let state = test_state();
    let scheduler = tokio::spawn(scheduler(state.clone(), catalog.url.clone()));
    let server = serve(app(state.clone())).await;
    for (path, payload, field) in [
        ("/search", serde_json::json!({"query":""}), "products"),
        ("/product", serde_json::json!({"id":2}), "product"),
    ] {
        let response = http()
            .post(format!("{}{path}", server.url))
            .bearer_auth("test-AGENT_TOKEN_1")
            .json(&payload)
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 200);
        assert!(
            response
                .json::<serde_json::Value>()
                .await
                .unwrap()
                .get(field)
                .is_some()
        );
    }
    slots(&identity(&state, "AGENT_TOKEN_1").budget, 5).await;
    scheduler.abort();
}

#[tokio::test]
async fn test_rejection_consumes_no_budget() {
    let state = test_state();
    state.reject_once.store(true, Ordering::Relaxed);
    let response = enqueue(
        state.clone(),
        identity(&state, "AGENT_TOKEN_1"),
        TaskAction::GetProduct(2),
    )
    .await;
    assert_eq!(response.status(), 429);
    assert_eq!(response.headers()["retry-after"], "2");
    assert_eq!(
        identity(&state, "AGENT_TOKEN_1").budget.available_permits(),
        5
    );
    assert!(state.queues.lock().await.agents.is_empty());
}

#[tokio::test]
async fn upstream_timeout_releases_budget() {
    let state = test_state();
    let mut upstream = upstream(StatusCode::OK).await;
    let scheduler = tokio::spawn(scheduler(state.clone(), upstream.server.url.clone()));
    let task = submit(&state, "AGENT_TOKEN_1", TaskAction::Search("".into()));
    timeout(Duration::from_secs(3), upstream.started.recv())
        .await
        .unwrap()
        .unwrap();
    let response = timeout(Duration::from_secs(7), task)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(response.status(), 504);
    slots(&identity(&state, "AGENT_TOKEN_1").budget, 5).await;
    scheduler.abort();
}

#[tokio::test]
async fn broken_upstream_body_releases_budget() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let broken = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = [0; 4096];
        socket.read(&mut request).await.unwrap();
        socket
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{")
            .await
            .unwrap();
    });
    let state = test_state();
    let scheduler = tokio::spawn(scheduler(state.clone(), url));
    let task = submit(&state, "AGENT_TOKEN_1", TaskAction::GetProduct(2));
    assert_eq!(finish(task).await.status(), 502);
    slots(&identity(&state, "AGENT_TOKEN_1").budget, 5).await;
    broken.await.unwrap();
    scheduler.abort();
}

#[tokio::test]
async fn interactive_budget_is_ten_and_shared_within_its_class() {
    let state = test_state();
    let interactive = identity(&state, "INTERACTIVE_TOKEN");
    let mut tasks = Vec::new();
    for _ in 0..10 {
        tasks.push(submit(
            &state,
            "INTERACTIVE_TOKEN",
            TaskAction::GetProduct(1),
        ));
    }
    slots(&interactive.budget, 0).await;
    let response = enqueue(
        state.clone(),
        interactive.clone(),
        TaskAction::GetProduct(1),
    )
    .await;
    assert_eq!(response.status(), 429);
    assert_eq!(
        identity(&state, "AGENT_TOKEN_1").budget.available_permits(),
        5
    );
    for task in tasks {
        task.abort();
        let _ = task.await;
    }
    state.queues.lock().await.prune_cancelled();
    slots(&interactive.budget, 10).await;
}

#[path = "dynamic_tests.rs"]
mod dynamic;

#[path = "queue_tests.rs"]
mod queueing;

#[test]
fn four_distinct_agent_credentials_share_the_same_resizable_budget() {
    let identities = identities_from(|name| Ok(format!("test-{name}"))).unwrap();
    let agents: Vec<_> = (1..=4)
        .map(|n| &identities[&format!("test-AGENT_TOKEN_{n}")])
        .collect();
    for agent in &agents {
        assert_eq!(agent.principal_id, "demo-owner");
        assert!(agent.class == ClientClass::Agent);
        assert_eq!(agent.operations, ["search_products", "get_product"]);
        assert!(Arc::ptr_eq(&agent.budget, &agents[0].budget));
    }
    let permits: Vec<_> = (0..5)
        .map(|n| agents[n % 4].budget.try_acquire().unwrap())
        .collect();
    assert!(agents.iter().all(|a| a.budget.try_acquire().is_none()));
    agents[0].budget.set_maximum(2);
    assert!(agents.iter().all(|a| a.budget.try_acquire().is_none()));
    drop(permits);
    assert!(agents.iter().all(|a| a.budget.snapshot().outstanding == 0));
    let permit = agents[3].budget.try_acquire().unwrap();
    assert_eq!(agents[0].budget.snapshot().outstanding, 1);
    drop(permit);
    agents[2].budget.set_maximum(5);
    assert!(agents.iter().all(|a| a.budget.snapshot().maximum == 5));
}
