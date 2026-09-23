use super::*;
use axum::{Json, Router, extract::State, http::HeaderMap, routing::get};
use std::sync::atomic::{AtomicUsize, Ordering};

struct Fixture {
    policy: Mutex<Value>,
    fixed_calls: AtomicUsize,
}
fn policy(limit: usize, revision: u64) -> Value {
    json!({"version":3,"principal_id":"demo-owner","client_class":"agent",
        "policy_revision":revision,"refresh_after_ms":100,"outstanding":0,
        "limits":{"scope":"principal","max_outstanding":limit}})
}
async fn wait_revision(gate: &Gate, revision: u64) {
    let mut changed = gate.subscribe();
    tokio::time::timeout(Duration::from_secs(3), async {
        while gate.snapshot().revision != revision {
            changed.changed().await.unwrap();
        }
    })
    .await
    .expect("Policy was not applied");
}

#[tokio::test]
async fn fixed_client_keeps_initial_gate_while_adaptive_client_applies_changes() {
    let fixture = Arc::new(Fixture {
        policy: Mutex::new(policy(5, 1)),
        fixed_calls: AtomicUsize::new(0),
    });
    let router = Router::new()
        .route(
            "/agent-policy",
            get(
                |State(state): State<Arc<Fixture>>, headers: HeaderMap| async move {
                    if headers["authorization"] == "Bearer fixed-test" {
                        state.fixed_calls.fetch_add(1, Ordering::SeqCst);
                    }
                    Json(state.policy.lock().unwrap().clone())
                },
            ),
        )
        .with_state(fixture.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let path = std::env::temp_dir().join(format!("bench-policy-test-{}.jsonl", std::process::id()));
    let log = Arc::new(Log {
        file: Mutex::new(File::create(&path).unwrap()),
        start: Instant::now(),
    });
    let http = Client::builder().no_proxy().build().unwrap();
    let fixed = Gate::new();
    let adaptive = Gate::new();
    let once = tokio::spawn(poll(
        http.clone(),
        base.clone(),
        "fixed-test".into(),
        "demo-owner",
        fixed.clone(),
        log.clone(),
        false,
    ));
    let refreshing = tokio::spawn(poll(
        http,
        base,
        "adaptive-test".into(),
        "demo-owner",
        adaptive.clone(),
        log.clone(),
        true,
    ));
    wait_revision(&fixed, 1).await;
    wait_revision(&adaptive, 1).await;
    once.await.unwrap();
    for (limit, revision) in [(2, 2), (5, 3)] {
        *fixture.policy.lock().unwrap() = policy(limit, revision);
        wait_revision(&adaptive, revision).await;
        assert_eq!(adaptive.snapshot().maximum, limit);
        assert_eq!(
            (fixed.snapshot().maximum, fixed.snapshot().revision),
            (5, 1)
        );
    }
    assert_eq!(fixture.fixed_calls.load(Ordering::SeqCst), 1);
    // Both modes still acquire and release permits through exactly the same Gate.
    let permit = fixed.acquire().await.unwrap();
    assert_eq!(fixed.snapshot().active, 1);
    drop(permit);
    assert_eq!(fixed.snapshot().active, 0);
    refreshing.abort();
    let _ = refreshing.await;
    server.abort();
    let _ = server.await;
    drop(log);
    std::fs::remove_file(path).unwrap();
}
