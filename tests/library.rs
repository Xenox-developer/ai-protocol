//! Exercise the library as an external crate, without accessing private modules.
use ai_protocol::{Gateway, GatewayConfig};
use axum::{Json, Router, routing::get};
use serde_json::{Value, json};
use std::time::Duration;
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
    let gateway = Gateway::start(config).await;
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
