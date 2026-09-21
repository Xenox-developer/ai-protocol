use reqwest::Client;
use std::time::Duration;
use tokio::task::JoinSet;
use tokio::time::{sleep_until, Instant};

#[tokio::main]
async fn main() {
    let client = Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap();

    println!("Test 1: humans only");
    generate(client.clone(), "human", 5).await;

    println!("\nTest 2: humans and agents concurrently");
    tokio::join!(
        generate(client.clone(), "human", 5),
        generate(client.clone(), "agent", 120),
    );
}

async fn generate(client: Client, kind: &'static str, rate: u64) {
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

async fn send_request(client: Client, kind: &str) -> Result<(), reqwest::Error> {
    let response = client
        .post("http://127.0.0.1:3000/search")
        .header("X-Client-Type", kind)
        .json(&serde_json::json!({"query": ""}))
        .send()
        .await?
        .error_for_status()?;

    // Read the entire response to measure the full request duration.
    response.bytes().await?;

    Ok(())
}
