use std::sync::Arc;
use reqwest::Client;
use std::time::Duration;
use tokio::task::JoinSet;
use tokio::sync::Semaphore;
use tokio::time::{sleep_until, Instant};

#[derive(serde::Deserialize)]
struct AgentPolicy {
    version: u32,
    max_in_flight: usize,
}

#[derive(Clone)]
struct LoadClient {
    http: Client,
    agent_slots: Arc<Semaphore>,
}

#[tokio::main]
async fn main() {
    let http = Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap();

    // Fetch the policy before starting the load test.
    let policy = http
        .get("http://127.0.0.1:3000/agent-policy")
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<AgentPolicy>()
        .await
        .unwrap();

    assert_eq!(policy.version, 2, "Unknown protocol version");
    assert!(
        policy.max_in_flight > 0 && policy.max_in_flight <= 1000,
        "Invalid concurrency limit"
    );

    println!("Server concurrency limit: {}", policy.max_in_flight);

    let client = LoadClient {
        http,
        agent_slots: Arc::new(Semaphore::new(policy.max_in_flight)),
    };

    println!("Test 1: humans only");
    generate(client.clone(), "human", 5).await;

    println!("\nTest 2: humans and agents concurrently");
    tokio::join!(
        generate(client.clone(), "human", 5),
        generate(client.clone(), "agent", 120),
    );
}

async fn generate(client: LoadClient, kind: &'static str, rate: u64) {
    let mut tasks = JoinSet::new();
    let start = Instant::now();

    // Send requests for 10 seconds.
    for i in 0..rate * 10 {
        // At 5 requests per second: 0, 200, 400, 600... milliseconds.
        let offset = Duration::from_secs_f64(i as f64 / rate as f64);
        let scheduled = start + offset;

        sleep_until(scheduled).await;

        let client = client.clone();

        // Run the request in a separate task and continue sending subsequent requests.
        tasks.spawn(async move {
            let sent = Instant::now();
            let result = send_request(client, kind).await;
            let elapsed = sent.elapsed().as_secs_f64() * 1000.0;

            (elapsed, result)
        });
    }

    let mut times = Vec::new();
    let mut errors = 0;

    // Wait for all requests, including those still in the queue.
    while let Some(task) = tasks.join_next().await {
        let (elapsed, result) = task.unwrap();

        match result {
            Ok(()) => times.push(elapsed),
            Err(error) => {
                if errors == 0 {
                    eprintln!("Reason: {error:?}");
                }
                errors += 1;
            }
        }
    }

    println!("{kind}: successful {}, errors {errors}", times.len());

    if !times.is_empty() {
        times.sort_by(|a, b| a.partial_cmp(b).unwrap());

        // Compute the 95th percentile position by rounding up.
        let index = (times.len() as f64 * 0.95).ceil() as usize - 1;

        println!("{kind}: p95 = {:.1} ms", times[index]);
    }
}

async fn send_request(
    client: LoadClient,
    kind: &str,
) -> Result<(), reqwest::Error> {
    // Human requests proceed immediately. Agent requests wait for a client-side slot.
    let _permit = if kind == "agent" {
        Some(client.agent_slots.acquire().await.unwrap())
    } else {
        None
    };

    let mut attempts = 0;

    loop {
        attempts += 1;

        let response = client
            .http
            .post("http://127.0.0.1:3000/search")
            .header("X-Client-Type", kind)
            .json(&serde_json::json!({"query": ""}))
            .send()
            .await?;

        if kind == "agent"
            && response.status() == reqwest::StatusCode::TOO_MANY_REQUESTS
            && attempts < 5
        {
            // Our protocol specifies Retry-After in whole seconds.
            let seconds = response
                .headers()
                .get("Retry-After")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(1);

            // Read the response body so the connection can be reused.
            response.bytes().await?;

            println!(
                "Received 429 on attempt {attempts}. Waiting {seconds} seconds."
            );

            let wait_started = Instant::now();

            tokio::time::sleep(Duration::from_secs(seconds)).await;

            println!(
                "Waited {:.2} seconds. Starting attempt {}.",
                wait_started.elapsed().as_secs_f64(),
                attempts + 1
            );

            continue;
        }

        response.error_for_status()?.bytes().await?;
        return Ok(());
    }
}
