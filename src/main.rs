use ai_protocol::{Gateway, GatewayConfig};
use std::{net::SocketAddr, time::Duration};

fn configuration() -> Result<(GatewayConfig, SocketAddr, Duration), Box<dyn std::error::Error>> {
    let config = GatewayConfig::from_env()?;
    let address = match std::env::var("GATEWAY_LISTEN_ADDR") {
        Ok(value) => value.parse()?,
        Err(std::env::VarError::NotPresent) => {
            let port = std::env::var("GATEWAY_PORT")
                .unwrap_or_else(|_| "3000".into())
                .parse::<u16>()?;
            if port == 0 {
                return Err("GATEWAY_PORT must be from 1 to 65535".into());
            }
            SocketAddr::from(([127, 0, 0, 1], port))
        }
        Err(error) => return Err(error.into()),
    };
    let grace = std::env::var("GATEWAY_SHUTDOWN_GRACE_MS")
        .unwrap_or_else(|_| "30000".into())
        .parse::<u64>()?;
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

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let (config, address, grace) = configuration()?;
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
    if run().await.is_err() {
        // Environment and I/O errors may contain credentials; keep startup failures generic.
        eprintln!(
            "{}",
            serde_json::json!({"event": "gateway_failed", "message": "Check configuration, listener, and shutdown grace"})
        );
        std::process::exit(1);
    }
}
