//! Open-loop benchmark client; A/B/C share scheduling, retry, and timeout code.
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::task::JoinSet;
use tokio::time::{Instant, sleep, sleep_until, timeout_at};
#[path = "load/control.rs"]
mod control;
#[path = "load/retry.rs"]
mod retry;
use control::{Gate, Policy};

#[derive(Clone, Deserialize, Serialize)]
struct Task {
    id: String,
    class: String,
    owner: String,
    arrival_ms: f64,
    operation: String,
    params: Value,
}
#[derive(Deserialize)]
struct Config {
    mode: String,
    start_unix_s: f64,
    run_timeout_s: f64,
    task_timeout_s: f64,
    max_pending: usize,
    tasks: Vec<Task>,
}
struct Log {
    file: Mutex<File>,
    start: Instant,
}
impl Log {
    fn emit(&self, mut value: Value) {
        value["at_ms"] = json!(self.start.elapsed().as_secs_f64() * 1000.0);
        let mut file = self.file.lock().unwrap();
        serde_json::to_writer(&mut *file, &value).expect("Cannot write client results");
        writeln!(file).expect("Cannot write client results");
    }
}
struct TaskLog {
    id: String,
    log: Arc<Log>,
    finished: bool,
}
impl TaskLog {
    fn finish(&mut self, outcome: &str, reason: &str) {
        self.log
            .emit(json!({"event":"terminal", "id":self.id, "outcome":outcome, "reason":reason}));
        self.finished = true;
    }
}
impl Drop for TaskLog {
    fn drop(&mut self) {
        if !self.finished {
            self.finish("unfinished", "run_deadline_or_cancelled");
        }
    }
}

async fn poll(
    http: Client,
    base: String,
    token: String,
    owner: &'static str,
    gate: Arc<Gate>,
    log: Arc<Log>,
) {
    loop {
        let result = async {
            let policy = http
                .get(format!("{base}/agent-policy"))
                .bearer_auth(&token)
                .timeout(Duration::from_secs(2))
                .send()
                .await
                .map_err(|_| ())?
                .error_for_status()
                .map_err(|_| ())?
                .json::<Policy>()
                .await
                .map_err(|_| ())?;
            let refresh = policy.refresh_after_ms;
            gate.apply(policy).map_err(|_| ())?;
            Ok::<_, ()>(refresh)
        }
        .await;
        if result.is_err() {
            gate.pause();
        }
        let state = gate.snapshot();
        log.emit(json!({"event":"policy", "owner":owner, "ready":state.ready,
            "limit":state.maximum, "revision":state.revision, "active":state.active}));
        sleep(Duration::from_millis(result.unwrap_or(1000))).await;
    }
}

async fn operation(
    task: Task,
    http: Client,
    base: String,
    token: String,
    gate: Option<Arc<Gate>>,
    log: Arc<Log>,
) -> Result<(), String> {
    let mut number = 0;
    retry::run(|| {
        number += 1;
        let task = task.clone();
        let http = http.clone();
        let base = base.clone();
        let token = token.clone();
        let gate = gate.clone();
        let log = log.clone();
        async move {
            let permit = match gate { Some(gate) => Some(gate.acquire().await?), None => None };
            log.emit(json!({"event":"attempt_start", "id":task.id, "number":number}));
            let result = async {
                let response = http.post(format!("{base}/{}", task.operation)).bearer_auth(token)
                    .json(&task.params).send().await.map_err(|_| "network_error".to_string())?;
                let status = response.status();
                let retry_after = response.headers().get("Retry-After").and_then(|v| v.to_str().ok()).map(str::to_owned);
                let body = response.bytes().await.map_err(|_| "network_error".to_string())?.to_vec();
                Ok::<_, String>(retry::Response { status, retry_after, body })
            }.await;
            drop(permit);
            match &result {
                Ok(response) => log.emit(json!({"event":"attempt_end", "id":task.id, "number":number,
                    "status":response.status.as_u16(), "retry_after":response.retry_after,
                    "error":serde_json::from_slice::<Value>(&response.body).ok().and_then(|v| v.get("error").cloned())})),
                Err(_) => log.emit(json!({"event":"attempt_end", "id":task.id, "number":number, "error_kind":"network_error"})),
            }
            result
        }
    }).await
}

#[tokio::main]
async fn main() {
    let args: Vec<_> = std::env::args().collect();
    assert_eq!(
        args.len(),
        3,
        "Usage: bench_client CONFIG.json EVENTS.jsonl"
    );
    assert!(
        std::fs::metadata(&args[1]).unwrap().len() <= 32 * 1024 * 1024,
        "Configuration too large"
    );
    let config: Config = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    assert!(["A", "B", "C"].contains(&config.mode.as_str()));
    assert!(config.tasks.len() <= 100_000 && (1..=10_000).contains(&config.max_pending));
    assert!(config.run_timeout_s > 0.0 && config.run_timeout_s <= 3600.0);
    assert!(config.task_timeout_s > 0.0 && config.task_timeout_s <= 3600.0);
    assert!(
        config
            .tasks
            .windows(2)
            .all(|pair| pair[0].arrival_ms <= pair[1].arrival_ms)
    );
    for task in &config.tasks {
        assert!(task.arrival_ms >= 0.0 && task.arrival_ms < config.run_timeout_s * 1000.0);
        assert!(["agent", "interactive"].contains(&task.class.as_str()));
        assert!(["demo-owner", "other-owner"].contains(&task.owner.as_str()));
        assert!(task.class != "interactive" || task.owner == "demo-owner");
        assert!(["search", "product"].contains(&task.operation.as_str()));
    }
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    assert!(config.start_unix_s > now, "Benchmark start was missed");
    let start = Instant::now() + Duration::from_secs_f64(config.start_unix_s - now);
    let log = Arc::new(Log {
        file: Mutex::new(
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&args[2])
                .unwrap(),
        ),
        start,
    });
    let http = Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap();
    let base = format!(
        "http://127.0.0.1:{}",
        std::env::var("GATEWAY_PORT").unwrap()
    );
    let agent = std::env::var("AGENT_TOKEN_1").unwrap();
    let other = std::env::var("OTHER_AGENT_TOKEN").unwrap();
    let interactive = std::env::var("INTERACTIVE_TOKEN").unwrap();
    let gates = [Gate::new(), Gate::new()];
    let mut pollers = JoinSet::new();
    if config.mode == "C" {
        for (owner, token, gate) in [
            ("demo-owner", agent.clone(), gates[0].clone()),
            ("other-owner", other.clone(), gates[1].clone()),
        ] {
            pollers.spawn(poll(
                http.clone(),
                base.clone(),
                token,
                owner,
                gate,
                log.clone(),
            ));
        }
    }
    let mut running = JoinSet::new();
    let work = async {
        for task in &config.tasks {
            sleep_until(start + Duration::from_secs_f64(task.arrival_ms / 1000.0)).await;
            while running.try_join_next().is_some() {}
            log.emit(json!({"event":"arrival", "id":task.id, "scheduled_ms":task.arrival_ms}));
            let mut guard = TaskLog {
                id: task.id.clone(),
                log: log.clone(),
                finished: false,
            };
            if running.len() >= config.max_pending {
                guard.finish("failed", "generator_capacity");
                continue;
            }
            let owner = usize::from(task.owner == "other-owner");
            let token = if task.class == "interactive" {
                interactive.clone()
            } else if owner == 0 {
                agent.clone()
            } else {
                other.clone()
            };
            let gate = (config.mode == "C" && task.class == "agent").then(|| gates[owner].clone());
            let deadline =
                start + Duration::from_secs_f64(task.arrival_ms / 1000.0 + config.task_timeout_s);
            let execution = operation(
                task.clone(),
                http.clone(),
                base.clone(),
                token,
                gate,
                log.clone(),
            );
            running.spawn(async move {
                match timeout_at(deadline, execution).await {
                    Ok(Ok(())) => guard.finish("success", "completed"),
                    Ok(Err(error)) => guard.finish("failed", &error),
                    Err(_) => guard.finish("failed", "task_deadline"),
                }
            });
        }
        while running.join_next().await.is_some() {}
    };
    let expired = timeout_at(start + Duration::from_secs_f64(config.run_timeout_s), work)
        .await
        .is_err();
    if expired {
        running.abort_all();
    }
    while running.join_next().await.is_some() {}
    for gate in gates {
        gate.stop();
    }
    pollers.abort_all();
    while pollers.join_next().await.is_some() {}
    log.emit(json!({"event":"run_end", "deadline_reached":expired}));
}
