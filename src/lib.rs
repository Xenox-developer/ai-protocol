//! Cooperative agent access to HTTP services with shared workload budgets.
//!
//! Start a [`Gateway`] with a [`GatewayConfig`], merge its router into your Axum
//! application, and keep the gateway alive while serving requests. The standalone
//! `ai-protocol` executable uses this same library and protocol v3 contract.
//!
//! ```no_run
//! use ai_protocol::{ClientClass, Credential, Gateway, GatewayConfig, Operation};
//! use axum::Json;
//! use serde::Deserialize;
//! use serde_json::json;
//!
//! #[derive(Deserialize)]
//! #[serde(deny_unknown_fields)]
//! struct EmptyInput {}
//!
//! # async fn run() -> Result<(), Box<dyn std::error::Error>> {
//! let operation = Operation::new(
//!     "list_documents", "List public document titles.", "/documents",
//!     json!({"type": "object", "properties": {}, "additionalProperties": false}),
//!     |_: EmptyInput| async {
//!         Json(json!({"documents": ["Rust introduction"]}))
//!     },
//! )?;
//! let config = GatewayConfig::native("documents", vec![operation], vec![Credential {
//!     token: std::env::var("DOCUMENT_TOKEN")?,
//!     principal_id: "reader".into(),
//!     client_class: ClientClass::Agent,
//!     operations: vec!["list_documents".into()],
//!     max_outstanding: 5,
//! }])?;
//! let gateway = Gateway::start(config).await?;
//! let router = axum::Router::new().merge(gateway.router());
//! // The application owns its listener and HTTP server lifecycle.
//! let listener = tokio::net::TcpListener::bind("127.0.0.1:3000").await?;
//! axum::serve(listener, router).await?;
//! gateway.shutdown().await?;
//! # Ok(())
//! # }
//! ```

use axum::{
    Extension, Json, Router,
    extract::{OriginalUri, Path, Request, State},
    http::{HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, patch, post},
};
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::sync::{Mutex, Notify, oneshot};

mod budget;
mod gateway;
pub use gateway::{ConfigError, Credential, Gateway, GatewayConfig};
mod operation;
pub use operation::Operation;
mod service;
pub use budget::Snapshot as BudgetSnapshot;
use budget::{Budget, Permit};
use tokio::task::JoinSet;
use tokio::time::{Instant, sleep_until};
mod queue;
use queue::Queues;
pub use queue::{QueueSettings, SchedulerMode};
mod telemetry;
use telemetry::Trace;

const EXECUTION_LIMIT: usize = 10;
const QUEUE_LIMIT: usize = 32;

/// Server-assigned scheduling class; callers cannot select it through HTTP headers.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ClientClass {
    /// Background agent work.
    Agent,
    /// Interactive work with weighted selection preference.
    Interactive,
}

#[derive(Clone)]
struct ClientIdentity {
    principal_id: String,
    class: ClientClass,
    operations: Vec<String>,
    budget: Arc<Budget>,
}

// Preserve the demo configuration for existing tests and launch commands.
#[cfg(test)]
fn identities_from(
    read: impl FnMut(&str) -> Result<String, std::env::VarError>,
) -> Result<HashMap<String, ClientIdentity>, String> {
    service::Service::catalog().identities(read)
}

enum TaskAction {
    Mapped(service::UpstreamRequest),
    Native(operation::Execution),
    #[cfg(test)]
    Search(String),
    #[cfg(test)]
    GetProduct(u64),
}

struct Job {
    reply: oneshot::Sender<Response>,
    action: TaskAction,
    permit: Permit,
    accepted_at: Instant,
    max_wait: Duration,
    order: u64,
}

impl Job {
    fn deadline(&self) -> Instant {
        self.accepted_at + self.max_wait
    }
}

struct AppState {
    service: service::Service,
    queues: Mutex<Queues>,
    notify: Notify,
    reject_once: AtomicBool,
    identities: HashMap<String, ClientIdentity>,
    admin_token: Option<String>,
    queue_settings: QueueSettings,
    trace: Option<Arc<Trace>>,
    active: Arc<std::sync::atomic::AtomicUsize>,
    closing: AtomicBool,
}

impl AppState {
    #[cfg(test)]
    fn new(
        identities: HashMap<String, ClientIdentity>,
        reject_once: bool,
        admin_token: Option<String>,
    ) -> Arc<Self> {
        Self::with_queue_settings(
            identities,
            reject_once,
            admin_token,
            QueueSettings::default(),
        )
    }

    #[cfg(test)]
    fn with_queue_settings(
        identities: HashMap<String, ClientIdentity>,
        reject_once: bool,
        admin_token: Option<String>,
        queue_settings: QueueSettings,
    ) -> Arc<Self> {
        Self::with_service(
            service::Service::catalog(),
            identities,
            reject_once,
            admin_token,
            queue_settings,
        )
    }

    fn with_service(
        service: service::Service,
        identities: HashMap<String, ClientIdentity>,
        reject_once: bool,
        admin_token: Option<String>,
        queue_settings: QueueSettings,
    ) -> Arc<Self> {
        Arc::new(Self {
            service,
            queues: Mutex::new(Queues::default()),
            notify: Notify::new(),
            reject_once: AtomicBool::new(reject_once),
            identities,
            admin_token,
            queue_settings,
            trace: None,
            active: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            closing: AtomicBool::new(false),
        })
    }

    fn agent_budget(&self, principal_id: &str) -> Option<&Arc<Budget>> {
        self.identities
            .values()
            .find(|identity| {
                identity.principal_id == principal_id && identity.class == ClientClass::Agent
            })
            .map(|identity| &identity.budget)
    }
}

async fn agent_policy(
    State(state): State<Arc<AppState>>,
    Extension(identity): Extension<ClientIdentity>,
) -> Json<serde_json::Value> {
    let operations: Vec<_> = state
        .service
        .operations
        .iter()
        .filter(|op| identity.operations.contains(&op.name))
        .map(service::Operation::descriptor)
        .collect();
    let budget = identity.budget.snapshot();
    Json(serde_json::json!({
        "version": 3,
        "service_id": state.service.service_id,
        "policy_revision": budget.revision,
        "refresh_after_ms": 1000,
        "outstanding": budget.outstanding,
        "queue": {"max_wait_ms": state.queue_settings.for_class(identity.class).as_millis() as u64},
        "principal_id": identity.principal_id,
        "client_class": identity.class,
        "limits": {
            "scope": "principal",
            "max_outstanding": budget.maximum
        },
        "operations": operations
    }))
}

fn app(state: Arc<AppState>) -> Router {
    let admin = Router::new()
        .route(
            "/admin/principals/{principal_id}/limits",
            patch(update_limits),
        )
        .route_layer(middleware::from_fn_with_state(
            state.clone(),
            authenticate_admin,
        ));
    let mut router = Router::new().route("/agent-policy", get(agent_policy));
    for operation in &state.service.operations {
        router = router.route(&operation.path, post(work));
    }
    router
        .route_layer(middleware::from_fn_with_state(state.clone(), authenticate))
        .merge(admin)
        .with_state(state)
}

async fn authenticate(
    State(state): State<Arc<AppState>>,
    mut request: Request,
    next: Next,
) -> Response {
    let identity = bearer_token(&request).and_then(|token| state.identities.get(token));

    let mut response = if let Some(identity) = identity {
        let operation = state
            .service
            .operations
            .iter()
            .find(|op| op.path == request.uri().path());
        if operation.is_some_and(|op| !identity.operations.contains(&op.name)) {
            (StatusCode::FORBIDDEN, "Operation is not permitted").into_response()
        } else {
            request.extensions_mut().insert(identity.clone());
            next.run(request).await
        }
    } else {
        (
            StatusCode::UNAUTHORIZED,
            [(header::WWW_AUTHENTICATE, "Bearer")],
            "A valid bearer token is required",
        )
            .into_response()
    };
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn bearer_token(request: &Request) -> Option<&str> {
    let mut headers = request.headers().get_all(header::AUTHORIZATION).iter();
    headers
        .next()
        .filter(|_| headers.next().is_none())
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split_once(' '))
        .filter(|(scheme, _)| scheme.eq_ignore_ascii_case("Bearer"))
        .map(|(_, token)| token)
}

fn admin_token_from(
    value: Result<String, std::env::VarError>,
    identities: &HashMap<String, ClientIdentity>,
) -> Result<Option<String>, String> {
    let token = match value {
        Ok(token) => token,
        Err(std::env::VarError::NotPresent) => return Ok(None),
        Err(_) => return Err("Invalid ADMIN_TOKEN".into()),
    };
    if token.is_empty()
        || !token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-._~+/=".contains(&b))
    {
        return Err("Invalid ADMIN_TOKEN".into());
    }
    if identities.contains_key(&token) {
        return Err("ADMIN_TOKEN must differ from all service tokens".into());
    }
    Ok(Some(token))
}

async fn authenticate_admin(
    State(state): State<Arc<AppState>>,
    request: Request,
    next: Next,
) -> Response {
    let token = bearer_token(&request);
    let mut response = match (&state.admin_token, token) {
        (None, _) => StatusCode::NOT_FOUND.into_response(),
        (Some(admin), Some(token)) if token == admin => next.run(request).await,
        (_, Some(token)) if state.identities.contains_key(token) => {
            StatusCode::FORBIDDEN.into_response()
        }
        _ => (
            StatusCode::UNAUTHORIZED,
            [(header::WWW_AUTHENTICATE, "Bearer")],
        )
            .into_response(),
    };
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct LimitsUpdate {
    max_outstanding: usize,
}

async fn update_limits(
    State(state): State<Arc<AppState>>,
    Path(principal_id): Path<String>,
    Json(update): Json<LimitsUpdate>,
) -> Response {
    if !(1..=1000).contains(&update.max_outstanding) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            "max_outstanding must be an integer from 1 to 1000",
        )
            .into_response();
    }
    let Some(budget) = state.agent_budget(&principal_id) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let budget = budget.set_maximum(update.max_outstanding);
    Json(serde_json::json!({
        "principal_id": principal_id,
        "client_class": "agent",
        "policy_revision": budget.revision,
        "limits": {"scope": "principal", "max_outstanding": budget.maximum},
        "outstanding": budget.outstanding
    }))
    .into_response()
}

fn overloaded(message: &'static str, delay: &'static str) -> Response {
    (
        StatusCode::TOO_MANY_REQUESTS,
        [(header::RETRY_AFTER, delay)],
        message,
    )
        .into_response()
}

async fn enqueue(state: Arc<AppState>, identity: ClientIdentity, action: TaskAction) -> Response {
    if identity.class == ClientClass::Agent && state.reject_once.swap(false, Ordering::Relaxed) {
        return overloaded("Test rejection", "2");
    }
    let (sender, receiver) = oneshot::channel();
    {
        let mut queues = state.queues.lock().await;
        if state.closing.load(Ordering::Relaxed) {
            return gateway_closed();
        }
        queues.maintain(Instant::now());
        // A nonblocking acquisition counts both queued and running work.
        let (permit, admitted) = match identity.budget.try_acquire_observed() {
            Some(admission) => admission,
            None => return overloaded("Owner budget exhausted", "1"),
        };
        let order = queues.take_order();
        let queue = match identity.class {
            ClientClass::Agent => &mut queues.agents,
            ClientClass::Interactive => &mut queues.interactive,
        };
        if queue.len() >= QUEUE_LIMIT {
            return overloaded("Queue is full", "1");
        }
        queue.push_back(Job {
            reply: sender,
            action,
            permit,
            accepted_at: Instant::now(),
            max_wait: state.queue_settings.for_class(identity.class),
            order,
        });
        if let Some(trace) = &state.trace {
            trace.emit(serde_json::json!({
                "event": "admission", "order": order,
                "principal_id": identity.principal_id, "class": identity.class,
                "outstanding": admitted.outstanding, "limit": admitted.maximum,
                "revision": admitted.revision
            }));
        }
    }
    state.notify.notify_one();
    receiver
        .await
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

async fn work(
    State(state): State<Arc<AppState>>,
    Extension(identity): Extension<ClientIdentity>,
    OriginalUri(uri): OriginalUri,
    Json(params): Json<serde_json::Value>,
) -> Response {
    let operation = state
        .service
        .operations
        .iter()
        .find(|op| op.path == uri.path())
        .unwrap();
    if let Some(native) = &operation.native {
        return match native.prepare(params) {
            Ok(execution) => enqueue(state, identity, TaskAction::Native(execution)).await,
            Err(response) => response,
        };
    }
    match operation.request(&params) {
        Ok(request) => enqueue(state, identity, TaskAction::Mapped(request)).await,
        Err(error) => (StatusCode::UNPROCESSABLE_ENTITY, error).into_response(),
    }
}

fn queue_timeout() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        [(header::RETRY_AFTER, "1")],
        Json(serde_json::json!({"error": {"code": "queue_timeout", "execution": "not_started"}})),
    )
        .into_response()
}

fn gateway_closed() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(serde_json::json!({"error": {"code": "gateway_closed", "execution": "not_started"}})),
    )
        .into_response()
}

fn upstream_client() -> Result<reqwest::Client, ConfigError> {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|_| ConfigError("Cannot initialize the upstream HTTP client".into()))
}

#[cfg(test)]
async fn scheduler(state: Arc<AppState>, upstream_url: String) {
    scheduler_with_client(state, Some(upstream_url), upstream_client().unwrap()).await;
}

async fn scheduler_with_client(
    state: Arc<AppState>,
    upstream_url: Option<String>,
    client: reqwest::Client,
) {
    run_scheduler(state, move |action| {
        let client = client.clone();
        let upstream_url = upstream_url.clone();
        async move { call_upstream(client, upstream_url.as_deref(), action).await }
    })
    .await;
}

async fn run_scheduler<F, Fut>(state: Arc<AppState>, execute: F)
where
    F: Fn(TaskAction) -> Fut,
    Fut: std::future::Future<Output = Response> + Send + 'static,
{
    let mut running = JoinSet::<()>::new();
    loop {
        while running.try_join_next().is_some() {}
        let deadline = {
            let mut queues = state.queues.lock().await;
            queues.maintain(Instant::now());
            while running.len() < EXECUTION_LIMIT {
                let Some(job) = queues.pop_next() else { break };
                let Job {
                    reply,
                    action,
                    permit,
                    ..
                } = job;
                let execution = execute(action);
                let active = telemetry::Active::new(state.active.clone());
                // The owned job is handed to an executor while the queue is
                // locked. There is no await between the expiry check and spawn.
                running.spawn(async move {
                    let response = execution.await;
                    drop(permit);
                    drop(active);
                    let _ = reply.send(response);
                });
            }
            if state.closing.load(Ordering::Relaxed)
                && running.is_empty()
                && queues.interactive.is_empty()
                && queues.agents.is_empty()
            {
                return;
            }
            queues.next_deadline()
        };
        tokio::select! {
            _ = state.notify.notified() => {}
            Some(_) = running.join_next(), if !running.is_empty() => {}
            _ = async {
                match deadline {
                    Some(deadline) => sleep_until(deadline).await,
                    None => std::future::pending::<()>().await,
                }
            } => {}
        }
    }
}

async fn call_upstream(
    client: reqwest::Client,
    upstream_url: Option<&str>,
    action: TaskAction,
) -> Response {
    let action = match action {
        TaskAction::Native(execution) => return execution.await,
        action => action,
    };
    let Some(upstream_url) = upstream_url else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    let request = match action {
        TaskAction::Native(_) => unreachable!("Native work was handled before HTTP mapping"),
        TaskAction::Mapped(request) => client
            .get(format!("{upstream_url}{}", request.path))
            .query(&request.query),
        #[cfg(test)]
        TaskAction::Search(query) => client
            .get(format!("{upstream_url}/products/search"))
            .query(&[("query", query)]),
        #[cfg(test)]
        TaskAction::GetProduct(id) => client
            .get(format!("{upstream_url}/products/get"))
            .query(&[("id", id)]),
    };

    let result = request.send().await;

    match result {
        Ok(response) => {
            // Treat an upstream error as a failure of the upstream service.
            if response.status() != reqwest::StatusCode::OK {
                return (StatusCode::BAD_GATEWAY, "Upstream returned an error").into_response();
            }

            match response.bytes().await {
                Ok(body) => {
                    (StatusCode::OK, [("Content-Type", "application/json")], body).into_response()
                }

                Err(_) => {
                    (StatusCode::BAD_GATEWAY, "Cannot read upstream response").into_response()
                }
            }
        }

        Err(error) => {
            if error.is_timeout() {
                (StatusCode::GATEWAY_TIMEOUT, "Upstream timeout").into_response()
            } else {
                (StatusCode::BAD_GATEWAY, "Upstream unavailable").into_response()
            }
        }
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod service_tests;
