//! Open-loop benchmark client; A/B/C/D share scheduling, retry, and timeout code.
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
use control::Gate;
#[path = "client/dispatch.rs"]
mod dispatch;
#[path = "client/transport.rs"]
mod transport;

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
        let now = Instant::now();
        let milliseconds = if now >= self.start {
            now.duration_since(self.start).as_secs_f64() * 1000.0
        } else {
            -self.start.duration_since(now).as_secs_f64() * 1000.0
        };
        value["at_ms"] = json!(milliseconds);
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
    refresh: bool,
) {
    loop {
        log.emit(json!({"event":"discovery_start", "owner":owner}));
        let result = async {
            let policy = transport::policy(&http, &base, &token)
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
        log.emit(json!({"event":"discovery_end", "owner":owner, "success":result.is_ok()}));
        let state = gate.snapshot();
        log.emit(json!({"event":"policy", "owner":owner, "ready":state.ready,
            "limit":state.maximum, "revision":state.revision, "active":state.active}));
        // D uses the same Gate and initial-discovery recovery as C, then freezes.
        if !refresh && result.is_ok() {
            return;
        }
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
            let result = transport::send(&http, &base, &token, &task.operation, &task.params).await;
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
    assert!(["A", "B", "C", "D", "S"].contains(&config.mode.as_str()));
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
    // A process discovers only the agent scopes present in its static partition.
    let used = ["demo-owner", "other-owner"].map(|owner| {
        config
            .tasks
            .iter()
            .any(|task| task.class == "agent" && task.owner == owner)
    });
    let credential = |needed, name| {
        if needed {
            std::env::var(name).expect("Missing task credential")
        } else {
            String::new()
        }
    };
    let agent = credential(used[0], "AGENT_TOKEN_1");
    let other = credential(used[1], "OTHER_AGENT_TOKEN");
    let interactive = credential(
        config.tasks.iter().any(|task| task.class == "interactive"),
        "INTERACTIVE_TOKEN",
    );
    let gates = [Gate::new(), Gate::new()];
    let mut pollers = JoinSet::new();
    if matches!(config.mode.as_str(), "C" | "D") {
        for (needed, owner, token, gate) in [
            (used[0], "demo-owner", agent.clone(), gates[0].clone()),
            (used[1], "other-owner", other.clone(), gates[1].clone()),
        ] {
            if !needed {
                continue;
            }
            pollers.spawn(poll(
                http.clone(),
                base.clone(),
                token,
                owner,
                gate,
                log.clone(),
                config.mode == "C",
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
            let gate = (matches!(config.mode.as_str(), "C" | "D") && task.class == "agent")
                .then(|| gates[owner].clone());
            let deadline =
                start + Duration::from_secs_f64(task.arrival_ms / 1000.0 + config.task_timeout_s);
            let dispatched = config.mode == "S" && task.class == "agent";
            let socket = if dispatched {
                Some(std::env::var("DISPATCH_SOCKET").expect("Set DISPATCH_SOCKET"))
            } else {
                None
            };
            let request = dispatch::Request {
                id: task.id.clone(),
                token: token.clone(),
                operation: task.operation.clone(),
                params: task.params.clone(),
                deadline_unix_ms: config.start_unix_s * 1000.0
                    + task.arrival_ms
                    + config.task_timeout_s * 1000.0,
            };
            let execution = operation(
                task.clone(),
                http.clone(),
                base.clone(),
                token,
                gate,
                log.clone(),
            );
            running.spawn(async move {
                if let Some(socket) = socket {
                    let reply = dispatch::submit(&socket, &request, deadline).await;
                    guard.log.emit(json!({"event":"dispatch_reply", "id":request.id,
                        "code":reply.code, "execution":reply.execution, "attempts":reply.attempts, "status":reply.status}));
                    guard.finish(if reply.ok { "success" } else { "failed" }, &reply.code);
                    return;
                }
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
    pollers.abort_all();
    while pollers.join_next().await.is_some() {}
    for (owner, gate) in ["demo-owner", "other-owner"].into_iter().zip(gates) {
        let state = gate.snapshot();
        log.emit(
            json!({"event":"gate_final", "owner":owner, "limit":state.maximum,
            "revision":state.revision, "active":state.active, "ready":state.ready}),
        );
        gate.stop();
    }
    log.emit(json!({"event":"run_end", "deadline_reached":expired}));
}

#[cfg(test)]
#[path = "bench/tests.rs"]
mod tests;
