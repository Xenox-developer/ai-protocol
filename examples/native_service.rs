//! A read-only service using only the public library interface.
use ai_protocol::{ClientClass, Credential, Gateway, GatewayConfig, Operation};
use axum::Json;
use serde::Deserialize;
use serde_json::json;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Lookup {
    query: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let token = std::env::var("NATIVE_AGENT_TOKEN")?;
    let operation = Operation::new(
        "find_documents",
        "Find public documents whose titles contain the query.",
        "/documents/find",
        json!({
            "type": "object", "properties": {"query": {"type": "string"}},
            "required": ["query"], "additionalProperties": false
        }),
        |input: Lookup| async move {
            let titles = ["Rust introduction", "Queue design", "HTTP services"];
            let documents: Vec<_> = titles
                .into_iter()
                .filter(|title| title.to_lowercase().contains(&input.query.to_lowercase()))
                .collect();
            Json(json!({"documents": documents}))
        },
    )?;
    let config = GatewayConfig::native(
        "documents",
        vec![operation],
        vec![Credential {
            token,
            principal_id: "document-reader".into(),
            client_class: ClientClass::Agent,
            operations: vec!["find_documents".into()],
            max_outstanding: 5,
        }],
    )?;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:3000").await?;
    let gateway = Gateway::start(config).await;
    println!("Native document service listening on 127.0.0.1:3000");
    axum::serve(listener, gateway.router()).await?;
    Ok(())
}
