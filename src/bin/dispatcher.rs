//! One local owner, one bounded attempt queue, one Gate, one policy refresher.
#[path = "load/control.rs"]
mod control;
#[path = "client/dispatch.rs"]
pub mod dispatch;
#[path = "load/retry.rs"]
mod retry;
#[path = "client/transport.rs"]
mod transport;

use control::Gate;
use dispatch::{MAX_FRAME, Reply, Request};
use reqwest::Client;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::{Semaphore, mpsc, oneshot};
use tokio::task::JoinSet;
use tokio::time::{Instant, sleep, sleep_until, timeout};

fn unix_ms() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
        * 1000.0
}
struct Trace {
    file: Option<Mutex<File>>,
    start: Instant,
    epoch_ms: f64,
}
impl Trace {
    fn emit(&self, mut value: Value) {
        if let Some(file) = &self.file {
            value["unix_s"] =
                json!((self.epoch_ms + self.start.elapsed().as_secs_f64() * 1000.0) / 1000.0);
            let mut file = file.lock().unwrap();
            serde_json::to_writer(&mut *file, &value).expect("Cannot write dispatcher trace");
            writeln!(file).expect("Cannot write dispatcher trace");
        }
    }
}
#[derive(Default)]
struct ProgressState {
    attempts: usize,
    may_have_executed: bool,
    closed: bool,
}
#[derive(Default)]
struct Progress(Mutex<ProgressState>);
impl Progress {
    // This lock linearizes cancellation/deadline reporting with dispatch.
    fn begin(&self, deadline: Instant) -> bool {
        let mut state = self.0.lock().unwrap();
        if state.closed || Instant::now() >= deadline {
            return false;
        }
        state.attempts += 1;
        state.may_have_executed = true;
        true
    }
    fn failure(&self, code: &str) -> Reply {
        let mut state = self.0.lock().unwrap();
        state.closed = true;
        Reply::error(
            code,
            if state.may_have_executed {
                "unknown"
            } else {
                "not_started"
            },
            state.attempts,
        )
    }
}
struct Cancellation(Arc<Progress>);
impl Drop for Cancellation {
    fn drop(&mut self) {
        self.0.0.lock().unwrap().closed = true;
    }
}
struct Attempt {
    request: Request,
    deadline: Instant,
    number: usize,
    progress: Arc<Progress>,
    reply: oneshot::Sender<Result<retry::Response, String>>,
}
struct Dispatcher {
    routes: HashMap<String, String>,
    service_id: Option<String>,
    http: Client,
    base: String,
    tokens: HashSet<String>,
    gate: Arc<Gate>,
    queue: mpsc::Sender<Attempt>,
    capacity: Arc<Semaphore>,
    trace: Arc<Trace>,
}

// Only these responses establish that the gateway did not start the operation.
fn not_started(response: &retry::Response) -> bool {
    matches!(response.status.as_u16(), 400 | 401 | 403 | 422 | 429)
        || (response.status.as_u16() == 503
            && serde_json::from_slice::<Value>(&response.body).is_ok_and(|v| {
                v["error"]["code"] == "queue_timeout" && v["error"]["execution"] == "not_started"
            }))
}

#[derive(serde::Deserialize)]
struct Discovery {
    #[serde(flatten)]
    policy: control::Policy,
    #[serde(default)]
    service_id: Option<String>,
    operations: Vec<OperationRoute>,
}
#[derive(serde::Deserialize)]
struct OperationRoute {
    name: String,
    method: String,
    path: String,
}
fn safe_component(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}
fn add_routes(
    routes: &mut HashMap<String, String>,
    operations: Vec<OperationRoute>,
) -> Result<(), &'static str> {
    let mut names = HashSet::new();
    for op in operations {
        if !safe_component(&op.name)
            || !names.insert(op.name.clone())
            || op.method != "POST"
            || !op
                .path
                .strip_prefix('/')
                .is_some_and(|p| p.split('/').all(safe_component))
        {
            return Err("Invalid discovery route");
        }
        // Retain legacy route aliases, but derive them from discovery, never client URLs.
        let path = op.path.trim_start_matches('/').to_string();
        for alias in [op.name, path.clone()] {
            if routes
                .insert(alias, path.clone())
                .is_some_and(|old| old != path)
            {
                return Err("Ambiguous discovery route");
            }
        }
    }
    Ok(())
}

impl Dispatcher {
    async fn initialize(
        http: Client,
        base: String,
        tokens: Vec<String>,
        capacity: usize,
        trace: Arc<Trace>,
    ) -> Result<(Arc<Self>, mpsc::Receiver<Attempt>), &'static str> {
        if tokens.is_empty()
            || tokens.len() > 64
            || !(1..=10_000).contains(&capacity)
            || tokens.iter().any(|token| token.is_empty())
            || tokens.iter().collect::<HashSet<_>>().len() != tokens.len()
        {
            return Err("Invalid dispatcher configuration");
        }
        let origin = reqwest::Url::parse(&base).map_err(|_| "Invalid gateway origin")?;
        if !matches!(origin.scheme(), "http" | "https")
            || origin.host_str().is_none()
            || !origin.username().is_empty()
            || origin.password().is_some()
            || origin.path() != "/"
            || origin.query().is_some()
            || origin.fragment().is_some()
        {
            return Err("Gateway must be an HTTP(S) origin");
        }
        let base = origin.as_str().trim_end_matches('/').to_owned();
        let gate = Gate::new();
        let mut routes = HashMap::new();
        let mut service_id = None;
        for (index, token) in tokens.iter().enumerate() {
            trace.emit(
                json!({"event":"discovery_start", "purpose":"identity", "credential_index":index}),
            );
            let fetched = transport::discovery::<Discovery>(&http, &base, token).await;
            trace.emit(json!({"event":"discovery_end", "purpose":"identity", "credential_index":index, "success":fetched.is_ok()}));
            let discovery = fetched?;
            if index == 0 {
                service_id = discovery.service_id.clone();
            }
            if discovery.service_id != service_id
                || service_id.as_ref().is_some_and(|id| id.is_empty())
            {
                return Err("Inconsistent service identity");
            }
            add_routes(&mut routes, discovery.operations)?;
            let policy = discovery.policy;
            // Gate validation rejects interactive, inconsistent, or different-owner identities.
            gate.apply(policy)?;
        }
        let (queue, receiver) = mpsc::channel(capacity);
        Ok((
            Arc::new(Self {
                routes,
                service_id,
                http,
                base,
                tokens: tokens.into_iter().collect(),
                gate,
                queue,
                capacity: Arc::new(Semaphore::new(capacity)),
                trace,
            }),
            receiver,
        ))
    }

    async fn poll(self: Arc<Self>, token: String) {
        loop {
            self.trace
                .emit(json!({"event":"discovery_start", "purpose":"refresh"}));
            let fetched = transport::discovery::<Discovery>(&self.http, &self.base, &token).await;
            let result = fetched.and_then(|discovery| {
                if discovery.service_id != self.service_id {
                    return Err("Service identity changed");
                }
                let policy = discovery.policy;
                let delay = policy.refresh_after_ms;
                self.gate.apply(policy).map(|_| delay)
            });
            if result.is_err() {
                self.gate.pause();
            }
            self.trace.emit(
                json!({"event":"discovery_end", "purpose":"refresh", "success":result.is_ok()}),
            );
            let state = self.gate.snapshot();
            self.trace.emit(
                json!({"event":"policy", "service_origin":self.base, "service_id":self.service_id, "owner":state.principal, "ready":state.ready,
                "limit":state.maximum, "revision":state.revision, "active":state.active}),
            );
            sleep(Duration::from_millis(result.unwrap_or(1000))).await;
        }
    }

    async fn execute(self: Arc<Self>, mut queue: mpsc::Receiver<Attempt>) {
        let mut running = JoinSet::new();
        while let Some(mut job) = queue.recv().await {
            while running.try_join_next().is_some() {}
            // Exactly one FIFO consumer waits for the shared Gate. Dropped receivers
            // cancel waiting attempts; already-started HTTP keeps its permit to EOF.
            let permit = tokio::select! {
                biased;
                _ = job.reply.closed() => continue,
                _ = sleep_until(job.deadline) => { let _ = job.reply.send(Err("task_deadline".into())); continue; },
                permit = self.gate.acquire() => match permit { Ok(p) => p, Err(_) => break },
            };
            if job.reply.is_closed() || !job.progress.begin(job.deadline) {
                let _ = job.reply.send(Err("task_deadline".into()));
                continue;
            }
            let state = self.gate.snapshot();
            self.trace.emit(
                json!({"event":"attempt_start", "id":job.request.id, "number":job.number,
                "local_active":state.active, "local_limit":state.maximum}),
            );
            let dispatcher = self.clone();
            running.spawn(async move {
                let result = transport::send(&dispatcher.http, &dispatcher.base, &job.request.token,
                    &job.request.operation, &job.request.params).await;
                if result.as_ref().is_ok_and(not_started) {
                    job.progress.0.lock().unwrap().may_have_executed = false;
                }
                drop(permit);
                match &result {
                    Ok(response) => dispatcher.trace.emit(json!({"event":"attempt_end", "id":job.request.id,
                        "number":job.number, "status":response.status.as_u16(), "retry_after":response.retry_after,
                        "error":serde_json::from_slice::<Value>(&response.body).ok().and_then(|v|v.get("error").cloned())})),
                    Err(_) => dispatcher.trace.emit(json!({"event":"attempt_end", "id":job.request.id,
                        "number":job.number, "error_kind":"network_error"})),
                }
                let _ = job.reply.send(result);
            });
        }
        while running.join_next().await.is_some() {}
    }

    async fn job(&self, request: Request, deadline: Instant, progress: Arc<Progress>) -> Reply {
        let last = Arc::new(Mutex::new(None));
        let mut number = 0;
        let result = retry::run(|| {
            number += 1;
            let request = request.clone();
            let progress = progress.clone();
            let last = last.clone();
            async move {
                *last.lock().unwrap() = None;
                let (reply, response) = oneshot::channel();
                self.queue
                    .send(Attempt {
                        request,
                        deadline,
                        number,
                        progress,
                        reply,
                    })
                    .await
                    .map_err(|_| "dispatcher_stopped".to_string())?;
                let result = response
                    .await
                    .map_err(|_| "dispatcher_stopped".to_string())??;
                *last.lock().unwrap() = Some((
                    result.status.as_u16(),
                    serde_json::from_slice::<Value>(&result.body).ok(),
                ));
                Ok(result)
            }
        })
        .await;
        let mut reply = match result {
            Ok(()) => Reply {
                ok: true,
                code: "completed".into(),
                execution: "completed".into(),
                attempts: Some(progress.0.lock().unwrap().attempts),
                status: None,
                body: None,
            },
            Err(error) => progress.failure(&error),
        };
        if let Some((status, body)) = last.lock().unwrap().take() {
            reply.status = Some(status);
            reply.body = body;
        }
        reply
    }

    async fn connection(
        self: Arc<Self>,
        stream: UnixStream,
        _slot: tokio::sync::OwnedSemaphorePermit,
    ) {
        let (reader, mut writer) = stream.into_split();
        let mut reader = BufReader::new(reader).take(MAX_FRAME + 1);
        let mut bytes = Vec::new();
        let read = timeout(Duration::from_secs(2), reader.read_until(b'\n', &mut bytes)).await;
        let parsed = if matches!(read, Ok(Ok(_)))
            && bytes.len() <= MAX_FRAME as usize
            && bytes.ends_with(b"\n")
        {
            serde_json::from_slice::<Request>(&bytes).ok()
        } else {
            None
        };
        let Some(mut request) = parsed else {
            send_reply(
                &mut writer,
                Reply::error("invalid_dispatch_request", "not_started", 0),
            )
            .await;
            return;
        };
        if !self.tokens.contains(&request.token) {
            send_reply(
                &mut writer,
                Reply::error("unregistered_credential", "not_started", 0),
            )
            .await;
            return;
        }
        let remaining = request.deadline_unix_ms - unix_ms();
        if !remaining.is_finite()
            || remaining > 3_600_000.0
            || request.id.is_empty()
            || request.id.len() > 128
            || !self.routes.contains_key(&request.operation)
        {
            send_reply(
                &mut writer,
                Reply::error("invalid_dispatch_request", "not_started", 0),
            )
            .await;
            return;
        }
        if remaining <= 0.0 {
            send_reply(&mut writer, Reply::error("task_deadline", "not_started", 0)).await;
            return;
        }
        request.operation = self.routes[&request.operation].clone();
        let deadline = Instant::now() + Duration::from_secs_f64(remaining / 1000.0);
        let progress = Arc::new(Progress::default());
        let _cancel = Cancellation(progress.clone());
        let mut extra = [0u8];
        let outcome = tokio::select! {
            biased;
            _ = reader.read(&mut extra) => { self.trace.emit(json!({"event":"caller_disconnected", "id":request.id})); return; },
            _ = sleep_until(deadline) => progress.failure("task_deadline"),
            result = self.job(request.clone(), deadline, progress.clone()) => result,
        };
        self.trace.emit(
            json!({"event":"dispatcher_terminal", "id":request.id, "code":outcome.code,
            "execution":outcome.execution, "attempts":outcome.attempts}),
        );
        send_reply(&mut writer, outcome).await;
    }
}

async fn send_reply(writer: &mut (impl AsyncWriteExt + Unpin), reply: Reply) {
    let mut bytes = serde_json::to_vec(&reply).unwrap();
    if bytes.len() >= MAX_FRAME as usize {
        bytes = serde_json::to_vec(&Reply::error(
            "dispatcher_response_too_large",
            "unknown",
            reply.attempts.unwrap_or(0),
        ))
        .unwrap();
    }
    bytes.push(b'\n');
    let _ = timeout(Duration::from_secs(1), writer.write_all(&bytes)).await;
}

async fn serve(dispatcher: Arc<Dispatcher>, listener: UnixListener) {
    let mut clients = JoinSet::new();
    loop {
        let Ok((mut stream, _)) = listener.accept().await else {
            break;
        };
        while clients.try_join_next().is_some() {}
        match dispatcher.capacity.clone().try_acquire_owned() {
            Ok(slot) => {
                clients.spawn(dispatcher.clone().connection(stream, slot));
            }
            Err(_) => {
                send_reply(
                    &mut stream,
                    Reply::error("dispatcher_queue_full", "not_started", 0),
                )
                .await
            }
        }
    }
}

#[tokio::main]
async fn main() {
    if run().await.is_err() {
        eprintln!(
            "Dispatcher failed; check private socket, credentials, gateway and configuration"
        );
        std::process::exit(1);
    }
}
async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let socket = std::env::var("DISPATCH_SOCKET")?;
    let parent = Path::new(&socket)
        .parent()
        .ok_or("Missing private socket directory")?;
    if !parent.is_dir() || parent.metadata()?.permissions().mode() & 0o077 != 0 {
        return Err("Socket directory must be private".into());
    }
    let names = std::env::var("DISPATCH_TOKEN_VARS")
        .unwrap_or_else(|_| "AGENT_TOKEN_1,AGENT_TOKEN_2".into());
    let tokens: Vec<String> = names
        .split(',')
        .map(|name| std::env::var(name.trim()))
        .collect::<Result<_, _>>()?;
    let policy_token = tokens.first().ok_or("Missing credentials")?.clone();
    let capacity = std::env::var("DISPATCH_CAPACITY")
        .unwrap_or_else(|_| "1024".into())
        .parse()?;
    let port: u16 = std::env::var("GATEWAY_PORT")
        .unwrap_or_else(|_| "3000".into())
        .parse()?;
    let file = std::env::var("DISPATCH_TRACE_PATH")
        .ok()
        .map(|path| {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(path)
        })
        .transpose()?
        .map(Mutex::new);
    let trace = Arc::new(Trace {
        file,
        start: Instant::now(),
        epoch_ms: unix_ms(),
    });
    let http = Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(30))
        .build()?;
    let (dispatcher, queue) = Dispatcher::initialize(
        http,
        std::env::var("SERVICE_URL").unwrap_or_else(|_| format!("http://127.0.0.1:{port}")),
        tokens,
        capacity,
        trace.clone(),
    )
    .await?;
    let listener = UnixListener::bind(&socket)?;
    std::fs::set_permissions(&socket, std::fs::Permissions::from_mode(0o600))?;
    let executor = tokio::spawn(dispatcher.clone().execute(queue));
    let poller = tokio::spawn(dispatcher.clone().poll(policy_token));
    println!("Dispatcher ready");
    tokio::select! { _ = serve(dispatcher.clone(), listener) => {}, _ = tokio::signal::ctrl_c() => {} }
    // Stop accepting; wait for started HTTP to finish, without replay or persistence.
    poller.abort();
    let _ = poller.await;
    dispatcher.gate.stop();
    let mut changed = dispatcher.gate.subscribe();
    while dispatcher.gate.snapshot().active != 0 {
        changed.changed().await?;
    }
    executor.abort();
    let _ = executor.await;
    trace.emit(json!({"event":"gate_final", "owner":dispatcher.gate.snapshot().principal,
        "active":0, "limit":dispatcher.gate.snapshot().maximum, "revision":dispatcher.gate.snapshot().revision}));
    std::fs::remove_file(socket)?;
    Ok(())
}

#[cfg(test)]
#[path = "dispatcher/tests.rs"]
mod tests;
