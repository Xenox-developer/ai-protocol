//! Exercise the library as an external crate, without accessing private modules.
use ai_protocol::{ClientClass, Credential, Gateway, GatewayConfig, Operation};
use axum::{Json, Router, routing::get};
use serde_json::{Value, json};
use std::{sync::Arc, time::Duration};
use tokio::{net::TcpListener, task::JoinHandle};

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
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    Server { url, task }
}

#[tokio::test]
async fn application_embeds_a_manifest_gateway_through_public_interfaces() {
    let upstream = serve(Router::new().route(
        "/api/records",
        get(|headers: axum::http::HeaderMap| async move {
            assert!(!headers.contains_key("authorization"));
            Json(json!({"records": ["archive"]}))
        }),
    ))
    .await;
    let manifest = json!({
        "service_id": "archive",
        "operations": [{
            "name": "list_records", "description": "List archive records.",
            "path": "/records", "upstream_path": "/api/records", "parameters": {}
        }],
        "credentials": [{
            "token_env": "ARCHIVE_TOKEN", "required": true,
            "principal_id": "archive-owner", "client_class": "agent",
            "operations": ["list_records"], "max_outstanding": 2
        }]
    });
    let config = GatewayConfig::from_manifest(&manifest.to_string(), &upstream.url, |_| {
        Ok("test-archive-token".into())
    })
    .unwrap();
    let gateway = Gateway::start(config).await.unwrap();
    let server = serve(
        Router::new()
            .route("/health", get(|| async { "ready" }))
            .merge(gateway.router()),
    )
    .await;
    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap();
    assert_eq!(
        http.get(format!("{}/health", server.url))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    assert_eq!(
        http.post(format!("{}/records", server.url))
            .json(&json!({}))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    let policy: Value = http
        .get(format!("{}/agent-policy", server.url))
        .bearer_auth("test-archive-token")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(policy["service_id"], "archive");
    assert_eq!(policy["operations"][0]["name"], "list_records");
    let response = http
        .post(format!("{}/records", server.url))
        .bearer_auth("test-archive-token")
        .json(&json!({}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["cache-control"], "no-store");
    assert_eq!(
        response.json::<Value>().await.unwrap(),
        json!({"records": ["archive"]})
    );
}

fn credential(token: &str, owner: &str, allowed: bool) -> Credential {
    Credential {
        token: token.into(),
        principal_id: owner.into(),
        client_class: ClientClass::Agent,
        operations: if allowed {
            vec!["lookup_record".into()]
        } else {
            vec![]
        },
        max_outstanding: 1,
    }
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeInput {
    filter: Filter,
    tags: Vec<String>,
    archived: Option<bool>,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Filter {
    query: String,
    wait: bool,
}

#[tokio::test]
async fn native_handlers_validate_before_admission_and_share_owner_budgets() {
    let release = Arc::new(tokio::sync::Semaphore::new(0));
    let gate = release.clone();
    let (started, mut executions) = tokio::sync::mpsc::unbounded_channel();
    let schema = json!({
        "type": "object", "additionalProperties": false,
        "properties": {
            "filter": {"type": "object", "additionalProperties": false,
                "properties": {"query": {"type": "string"}, "wait": {"type": "boolean"}},
                "required": ["query", "wait"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "archived": {"type": ["boolean", "null"]}
        },
        "required": ["filter", "tags"]
    });
    let operation = Operation::new("lookup_record", "Look up a public record.", "/records/lookup", schema.clone(), move |input: NativeInput| {
        let release = gate.clone();
        let started = started.clone();
        async move {
            started.send(()).unwrap();
            if input.filter.wait {
                release.acquire().await.unwrap().forget();
            }
            Json(json!({"query": input.filter.query, "tags": input.tags, "archived": input.archived}))
        }
    }).unwrap();
    let config = GatewayConfig::native(
        "records",
        vec![operation],
        vec![
            credential("test-first", "reader", true),
            credential("test-second", "reader", true),
            credential("test-denied", "reader", false),
            credential("test-other", "other", true),
        ],
    )
    .unwrap()
    .with_admin_token("test-admin".into())
    .unwrap();
    let gateway = Gateway::start(config).await.unwrap();
    let server = serve(gateway.router()).await;
    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap();
    let url = format!("{}/records/lookup", server.url);
    let body = json!({"filter": {"query": "rust", "wait": true}, "tags": ["public"]});
    let first = tokio::spawn(http.post(&url).bearer_auth("test-first").json(&body).send());
    tokio::time::timeout(Duration::from_secs(3), executions.recv())
        .await
        .unwrap()
        .unwrap();
    let policy: Value = http
        .get(format!("{}/agent-policy", server.url))
        .bearer_auth("test-second")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(policy["operations"][0]["input_schema"], schema);
    assert_eq!(policy["outstanding"], 1);
    assert_eq!(
        http.post(&url)
            .bearer_auth("test-second")
            .json(&body)
            .send()
            .await
            .unwrap()
            .status(),
        429
    );
    // Validation and permissions must run before the exhausted-budget check.
    for invalid in [
        json!({}),
        json!({"filter": {"query": "rust", "wait": "yes"}, "tags": []}),
        json!({"filter": {"query": "rust", "wait": false}, "tags": [], "extra": true}),
    ] {
        assert_eq!(
            http.post(&url)
                .bearer_auth("test-first")
                .json(&invalid)
                .send()
                .await
                .unwrap()
                .status(),
            422
        );
    }
    assert_eq!(
        http.post(&url)
            .bearer_auth("test-denied")
            .body("invalid JSON")
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    assert!(executions.try_recv().is_err());
    let independent = http
        .post(&url)
        .bearer_auth("test-other")
        .json(&json!({"filter": {"query": "other", "wait": false}, "tags": [], "archived": true}))
        .send()
        .await
        .unwrap();
    assert_eq!(independent.status(), 200);
    assert_eq!(independent.json::<Value>().await.unwrap()["archived"], true);
    release.add_permits(1);
    let response = first.await.unwrap().unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(
        response.json::<Value>().await.unwrap(),
        json!({"query": "rust", "tags": ["public"], "archived": null})
    );
    let second = http
        .post(&url)
        .bearer_auth("test-second")
        .json(&json!({"filter": {"query": "again", "wait": false}, "tags": []}))
        .send()
        .await
        .unwrap();
    assert_eq!(second.status(), 200);
}

fn empty_operation(name: &str, path: &str) -> Result<Operation, ai_protocol::ConfigError> {
    Operation::new(
        name,
        "A read-only test operation.",
        path,
        json!({"type": "object"}),
        |_: Value| async { Json(json!({})) },
    )
}

#[test]
fn native_configuration_rejects_invalid_routes_grants_and_shared_limits() {
    for path in [
        "/agent-policy",
        "/admin",
        "/admin/limits",
        "/{capture}",
        "//other",
        "/../other",
    ] {
        assert!(empty_operation("lookup_record", path).is_err());
    }
    assert!(
        GatewayConfig::native(
            "records",
            vec![
                empty_operation("lookup_record", "/lookup").unwrap(),
                empty_operation("lookup_record", "/other").unwrap()
            ],
            vec![credential("test-one", "reader", true)]
        )
        .is_err()
    );
    assert!(
        GatewayConfig::native(
            "records",
            vec![
                empty_operation("lookup_record", "/lookup").unwrap(),
                empty_operation("other", "/lookup").unwrap()
            ],
            vec![credential("test-one", "reader", true)]
        )
        .is_err()
    );
    let mut mismatched = credential("test-two", "reader", true);
    mismatched.max_outstanding = 2;
    assert!(
        GatewayConfig::native(
            "records",
            vec![empty_operation("lookup_record", "/lookup").unwrap()],
            vec![credential("test-one", "reader", true), mismatched]
        )
        .is_err()
    );
    let invalid = Credential {
        token: "secret-invalid token".into(),
        ..credential("unused", "reader", true)
    };
    let error = GatewayConfig::native(
        "records",
        vec![empty_operation("lookup_record", "/lookup").unwrap()],
        vec![invalid],
    )
    .err()
    .unwrap();
    assert!(!error.to_string().contains("secret-invalid"));
    assert!(
        GatewayConfig::native(
            "records",
            vec![empty_operation("unrelated", "/lookup").unwrap()],
            vec![credential("test-one", "reader", true)]
        )
        .is_err()
    );
}

async fn exercise_lifecycle(graceful: bool) {
    let release = Arc::new(tokio::sync::Semaphore::new(0));
    let gate = release.clone();
    let (started, mut executions) = tokio::sync::mpsc::unbounded_channel();
    let operation = Operation::new(
        "lookup_record",
        "Read a record.",
        "/lookup",
        json!({"type": "object"}),
        move |_: Value| {
            let gate = gate.clone();
            let started = started.clone();
            async move {
                started.send(()).unwrap();
                gate.acquire().await.unwrap().forget();
                Json(json!({"ok": true}))
            }
        },
    )
    .unwrap();
    let config = GatewayConfig::native(
        "records",
        vec![operation],
        vec![Credential {
            max_outstanding: 20,
            ..credential("test-lifecycle", "reader", true)
        }],
    )
    .unwrap();
    let gateway = Gateway::start(config).await.unwrap();
    let server = serve(gateway.router()).await;
    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    let url = format!("{}/lookup", server.url);
    let mut requests = Vec::new();
    for _ in 0..11 {
        requests.push(tokio::spawn(
            http.post(&url)
                .bearer_auth("test-lifecycle")
                .json(&json!({}))
                .send(),
        ));
    }
    for _ in 0..10 {
        tokio::time::timeout(Duration::from_secs(3), executions.recv())
            .await
            .unwrap()
            .unwrap();
    }
    let policy_url = format!("{}/agent-policy", server.url);
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let policy: Value = http
                .get(&policy_url)
                .bearer_auth("test-lifecycle")
                .send()
                .await
                .unwrap()
                .json()
                .await
                .unwrap();
            if policy["outstanding"] == 11 {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    let snapshot = gateway.set_agent_limit("reader", 2).unwrap();
    assert_eq!(snapshot.maximum, 2);
    assert_eq!(snapshot.outstanding, 11);
    assert_eq!(snapshot.revision, 2);
    assert_eq!(gateway.set_agent_limit("reader", 2).unwrap().revision, 2);
    assert!(gateway.set_agent_limit("missing", 2).is_err());
    assert!(gateway.set_agent_limit("reader", 0).is_err());
    assert!(executions.try_recv().is_err());

    let draining = if graceful {
        gateway.close().await;
        Some(tokio::spawn(gateway.shutdown()))
    } else {
        drop(gateway);
        None
    };
    let rejected = http
        .post(&url)
        .bearer_auth("test-lifecycle")
        .json(&json!({}))
        .send()
        .await
        .unwrap();
    assert_eq!(rejected.status(), 503);
    assert_eq!(rejected.headers()["cache-control"], "no-store");
    assert_eq!(
        rejected.json::<Value>().await.unwrap(),
        json!({"error": {"code": "gateway_closed", "execution": "not_started"}})
    );
    if let Some(draining) = draining {
        assert!(!draining.is_finished());
        release.add_permits(10);
        tokio::time::timeout(Duration::from_secs(3), executions.recv())
            .await
            .unwrap()
            .unwrap();
        assert!(!draining.is_finished());
        release.add_permits(1);
        for request in requests {
            assert_eq!(request.await.unwrap().unwrap().status(), 200);
        }
        tokio::time::timeout(Duration::from_secs(3), draining)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
    } else {
        let mut statuses = Vec::new();
        for request in requests {
            statuses.push(request.await.unwrap().unwrap().status().as_u16());
        }
        assert_eq!(statuses.iter().filter(|&&status| status == 500).count(), 10);
        assert_eq!(statuses.iter().filter(|&&status| status == 503).count(), 1);
        assert!(executions.try_recv().is_err());
    }
    let policy: Value = http
        .get(&policy_url)
        .bearer_auth("test-lifecycle")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(policy["outstanding"], 0);
}

#[tokio::test]
async fn shutdown_drains_accepted_work_after_budget_shrink() {
    exercise_lifecycle(true).await;
}

#[tokio::test]
async fn dropping_gateway_rejects_pending_work_and_closes_a_retained_router() {
    exercise_lifecycle(false).await;
}

#[test]
fn starting_without_a_tokio_runtime_returns_an_error() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    let config = GatewayConfig::native(
        "records",
        vec![empty_operation("lookup_record", "/lookup").unwrap()],
        vec![credential("test-one", "reader", true)],
    )
    .unwrap();
    let mut start = Box::pin(Gateway::start(config));
    let mut context = Context::from_waker(Waker::noop());
    assert!(matches!(
        start.as_mut().poll(&mut context),
        Poll::Ready(Err(_))
    ));
}
