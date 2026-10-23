use ai_protocol::{Gateway, GatewayConfig};

#[tokio::main]
async fn main() {
    let config = GatewayConfig::from_env().unwrap_or_else(|error| {
        eprintln!("Configuration error: {error}");
        std::process::exit(1);
    });
    let port = match std::env::var("GATEWAY_PORT") {
        Ok(value) => value.parse::<u16>().ok().filter(|port| *port > 0),
        Err(std::env::VarError::NotPresent) => Some(3000),
        Err(_) => None,
    }
    .unwrap_or_else(|| {
        eprintln!("Configuration error: GATEWAY_PORT must be a port from 1 to 65535");
        std::process::exit(1);
    });
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
        .await
        .unwrap();
    let gateway = Gateway::start(config).await.unwrap_or_else(|error| {
        eprintln!("Cannot start gateway: {error}");
        std::process::exit(1);
    });
    println!("Server started on port {port} (protocol v3)");
    axum::serve(listener, gateway.router()).await.unwrap();
}
