use ai_protocol::{Gateway, GatewayConfig};
use std::{net::SocketAddr, time::Duration};

fn configuration() -> Result<(GatewayConfig, SocketAddr, Duration), Box<dyn std::error::Error>> {
    let config = GatewayConfig::from_env()?;
    let address = match std::env::var("GATEWAY_LISTEN_ADDR") {
        Ok(value) => value
            .parse()
            .map_err(|_| "Invalid GATEWAY_LISTEN_ADDR; expected IP:port")?,
        Err(std::env::VarError::NotPresent) => {
            let port = setting("GATEWAY_PORT", "3000")?
                .parse::<u16>()
                .map_err(|_| "GATEWAY_PORT must be from 1 to 65535")?;
            if port == 0 {
                return Err("GATEWAY_PORT must be from 1 to 65535".into());
            }
            SocketAddr::from(([127, 0, 0, 1], port))
        }
        Err(_) => return Err("Invalid GATEWAY_LISTEN_ADDR".into()),
    };
    let grace = setting("GATEWAY_SHUTDOWN_GRACE_MS", "30000")?
        .parse::<u64>()
        .map_err(|_| "GATEWAY_SHUTDOWN_GRACE_MS must be an integer")?;
    if !(1..=3600000).contains(&grace) {
        return Err("Invalid GATEWAY_SHUTDOWN_GRACE_MS".into());
    }
    Ok((config, address, Duration::from_millis(grace)))
}

async fn shutdown_signal() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(not(unix))]
    tokio::signal::ctrl_c().await
}

fn setting(name: &str, default: &str) -> Result<String, Box<dyn std::error::Error>> {
    match std::env::var(name) {
        Ok(value) => Ok(value),
        Err(std::env::VarError::NotPresent) => Ok(default.into()),
        Err(_) => Err(format!("Invalid {name}").into()),
    }
}

async fn run(
    config: GatewayConfig,
    address: SocketAddr,
    grace: Duration,
) -> Result<(), Box<dyn std::error::Error>> {
    let listener = tokio::net::TcpListener::bind(address).await?;
    let port = listener.local_addr()?.port();
    let gateway = Gateway::start(config).await?;
    let (stop, stopped) = tokio::sync::oneshot::channel();
    let server = axum::serve(listener, gateway.router()).with_graceful_shutdown(async {
        let _ = stopped.await;
    });
    let server = std::future::IntoFuture::into_future(server);
    tokio::pin!(server);
    println!("Server started on port {port} (protocol v3)");
    tokio::select! {
        result = &mut server => { result?; gateway.shutdown().await?; return Ok(()); }
        result = shutdown_signal() => { result?; }
    }
    gateway.close().await;
    let _ = stop.send(());
    tokio::time::timeout(grace, async {
        server.await?;
        gateway.shutdown().await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
    .await
    .map_err(|_| "Gateway shutdown grace expired; remote outcomes may be unknown")??;
    eprintln!("{}", serde_json::json!({"event": "gateway_stopped"}));
    Ok(())
}

#[tokio::main]
async fn main() {
    let (config, address, grace) = configuration().unwrap_or_else(|error| {
        // Configuration errors deliberately contain field names, never values.
        eprintln!("{}", serde_json::json!({"event": "gateway_configuration_failed", "message": error.to_string()}));
        std::process::exit(1);
    });
    if run(config, address, grace).await.is_err() {
        // Environment and I/O errors may contain credentials; keep startup failures generic.
        eprintln!(
            "{}",
            serde_json::json!({"event": "gateway_failed", "message": "Check configuration, listener, and shutdown grace"})
        );
        std::process::exit(1);
    }
}
