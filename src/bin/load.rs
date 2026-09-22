use reqwest::Client;
use std::sync::Arc;
use std::time::Duration;
use tokio::task::JoinSet;
use tokio::time::{Instant, sleep, sleep_until};

#[path = "load/control.rs"]
mod control;
use control::{Gate, Policy};
#[path = "load/retry.rs"]
mod retry;

#[derive(Clone)]
struct LoadClient {
    http: Client,
    base_url: String,
    gate: Arc<Gate>,
    agent_token: String,
    interactive_token: Option<String>,
}

fn number(name: &str, default: u64, maximum: u64) -> u64 {
    let value = std::env::var(name).unwrap_or_else(|_| default.to_string());
    value
        .parse::<u64>()
        .ok()
        .filter(|value| (1..=maximum).contains(value))
        .unwrap_or_else(|| {
            eprintln!("{name} must be an integer from 1 to {maximum}");
            std::process::exit(1);
        })
}

#[tokio::main]
async fn main() {
    let agent_token = std::env::var("AGENT_TOKEN_1").expect("Set AGENT_TOKEN_1");
    let batch = std::env::var_os("LOAD_AGENT_TASKS").map(|_| number("LOAD_AGENT_TASKS", 60, 10000));
    let interactive_token = if batch.is_none() {
        Some(std::env::var("INTERACTIVE_TOKEN").expect("Set INTERACTIVE_TOKEN"))
    } else {
        None
    };
    let port = number("GATEWAY_PORT", 3000, 65535);
    let deadline = Duration::from_secs(number("LOAD_DEADLINE_SECS", 120, 86400));
    let client = LoadClient {
        http: Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30))
            .build()
            .unwrap(),
        base_url: format!("http://127.0.0.1:{port}"),
        gate: Gate::new(),
        agent_token,
        interactive_token,
    };
    // One poller per process, independent of all operation permits and retries.
    let poller = tokio::spawn(poll_policy(client.clone()));
    let workload = async {
        if let Some(count) = batch {
            generate(client.clone(), "agent", count, None).await
        } else {
            println!("Test 1: interactive clients only");
            let baseline = generate(client.clone(), "interactive", 50, Some(5)).await;
            println!("Test 2: interactive clients and agents concurrently");
            let (interactive, agent) = tokio::join!(
                generate(client.clone(), "interactive", 50, Some(5)),
                generate(client.clone(), "agent", 1200, Some(120)),
            );
            baseline + interactive + agent
        }
    };
    let success = tokio::select! {
        errors = workload => errors == 0,
        _ = sleep(deadline) => { eprintln!("Workload deadline exceeded"); false },
        _ = tokio::signal::ctrl_c() => { eprintln!("Client interrupted"); false },
    };
    client.gate.stop();
    poller.abort();
    let _ = poller.await;
    if !success {
        std::process::exit(1);
    }
}

async fn fetch_policy(client: &LoadClient) -> Result<Policy, &'static str> {
    client
        .http
        .get(format!("{}/agent-policy", client.base_url))
        .bearer_auth(&client.agent_token)
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .map_err(|_| "Policy transport error")?
        .error_for_status()
        .map_err(|_| "Policy HTTP error")?
        .json()
        .await
        .map_err(|_| "Invalid policy JSON")
}

async fn refresh_policy(client: &LoadClient) -> Duration {
    let result = match fetch_policy(client).await {
        Ok(policy) => {
            let delay = Duration::from_millis(policy.refresh_after_ms);
            client.gate.apply(policy).map(|_| delay)
        }
        Err(error) => Err(error),
    };
    let (event, delay) = match result {
        Ok(delay) => ("policy", delay),
        Err(_) => {
            client.gate.pause();
            ("policy_error", Duration::from_secs(1))
        }
    };
    let state = client.gate.snapshot();
    println!(
        "{}",
        serde_json::json!({
            "event": event, "policy_revision": state.revision, "limit": state.maximum,
            "server_outstanding": state.server_outstanding, "client_active": state.active,
            "paused": !state.ready
        })
    );
    delay
}

async fn poll_policy(client: LoadClient) {
    loop {
        let delay = refresh_policy(&client).await;
        sleep(delay).await;
    }
}

async fn generate(client: LoadClient, kind: &'static str, count: u64, rate: Option<u64>) -> usize {
    let mut tasks = JoinSet::new();
    let start = Instant::now();
    for i in 0..count {
        if let Some(rate) = rate {
            sleep_until(start + Duration::from_secs_f64(i as f64 / rate as f64)).await;
        }
        let client = client.clone();
        tasks.spawn(async move {
            let sent = Instant::now();
            let result = send_request(client, kind).await;
            (sent.elapsed().as_secs_f64() * 1000.0, result)
        });
    }
    let mut times = Vec::new();
    let mut errors = 0;
    while let Some(task) = tasks.join_next().await {
        let (elapsed, result) = task.unwrap();
        match result {
            Ok(()) => times.push(elapsed),
            Err(error) => {
                if errors == 0 {
                    eprintln!("Reason: {error}");
                }
                errors += 1;
            }
        }
    }
    println!(
        "{}",
        serde_json::json!({"event":"completed", "class":kind, "successful":times.len(), "errors":errors})
    );
    if !times.is_empty() {
        times.sort_by(f64::total_cmp);
        let index = (times.len() as f64 * 0.95).ceil() as usize - 1;
        println!("{kind}: p95 = {:.1} ms", times[index]);
    }
    errors
}

async fn send_request(client: LoadClient, kind: &str) -> Result<(), String> {
    retry::run(|| async {
        let permit = if kind == "agent" {
            Some(client.gate.acquire().await?)
        } else {
            None
        };
        let token = if kind == "agent" {
            &client.agent_token
        } else {
            client
                .interactive_token
                .as_ref()
                .ok_or("Missing interactive credentials")?
        };
        let response = client
            .http
            .post(format!("{}/search", client.base_url))
            .bearer_auth(token)
            .json(&serde_json::json!({"query":""}))
            .send()
            .await
            .map_err(|_| "Operation transport error")?;
        let status = response.status();
        let retry_after = response
            .headers()
            .get("Retry-After")
            .and_then(|header| header.to_str().ok())
            .map(str::to_owned);
        let body = response
            .bytes()
            .await
            .map_err(|_| "Cannot read operation response")?
            .to_vec();
        drop(permit); // Every retry reacquires current capacity after the delay.
        Ok(retry::Response {
            status,
            retry_after,
            body,
        })
    })
    .await
}

#[cfg(test)]
#[path = "load/tests.rs"]
mod tests;
