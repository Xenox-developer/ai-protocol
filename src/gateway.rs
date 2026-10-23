use crate::{AppState, QueueSettings, SchedulerMode, app, service, telemetry};
use std::{collections::HashMap, fmt, sync::Arc, time::Duration};
use tokio::task::JoinHandle;

/// Gateway configuration or initialization failure, without credential values.
#[derive(Debug)]
pub struct ConfigError(pub(crate) String);

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ConfigError {}

impl From<String> for ConfigError {
    fn from(message: String) -> Self {
        Self(message)
    }
}

/// One host-provided bearer credential and its authorization grant.
///
/// Tokens of the same owner and class must have the same initial budget. The
/// gateway creates one shared counter for them, not one counter per credential.
/// This type intentionally does not implement `Debug` to avoid exposing tokens.
pub struct Credential {
    /// Secret bearer credential. Never include it in logs or discovery output.
    pub token: String,
    /// Owner identifier used for shared budget accounting.
    pub principal_id: String,
    /// Trusted, server-assigned scheduling class.
    pub client_class: crate::ClientClass,
    /// Names of operations this credential may invoke.
    pub operations: Vec<String>,
    /// Initial outstanding-work limit, from 1 through 1000.
    pub max_outstanding: usize,
}

pub(crate) fn valid_token(token: &str) -> bool {
    !token.is_empty()
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-._~+/=".contains(&b))
}

pub(crate) fn identities(
    credentials: Vec<Credential>,
    operations: &[service::Operation],
) -> Result<HashMap<String, crate::ClientIdentity>, String> {
    let mut identities = HashMap::new();
    let mut budgets: HashMap<_, Arc<crate::Budget>> = HashMap::new();
    for credential in credentials {
        if !valid_token(&credential.token) {
            return Err("Invalid bearer token".into());
        }
        if credential.principal_id.is_empty()
            || !(1..=1000).contains(&credential.max_outstanding)
            || credential
                .operations
                .iter()
                .any(|name| !operations.iter().any(|op| &op.name == name))
        {
            return Err("Invalid credential or permission mapping".into());
        }
        let budget = budgets
            .entry((credential.principal_id.clone(), credential.client_class))
            .or_insert_with(|| crate::Budget::new(credential.max_outstanding));
        if budget.snapshot().maximum != credential.max_outstanding {
            return Err("Tokens of one owner and class must specify the same budget".into());
        }
        if identities
            .insert(
                credential.token,
                crate::ClientIdentity {
                    principal_id: credential.principal_id,
                    class: credential.client_class,
                    operations: credential.operations,
                    budget: budget.clone(),
                },
            )
            .is_some()
        {
            return Err("Token values must be distinct".into());
        }
    }
    if identities.is_empty() {
        return Err("At least one credential is required".into());
    }
    Ok(identities)
}

/// Validated service, credentials, and queue configuration for one gateway.
///
/// Configuration owns secrets and intentionally does not implement `Debug`.
pub struct GatewayConfig {
    service: service::Service,
    identities: std::collections::HashMap<String, crate::ClientIdentity>,
    upstream_url: Option<String>,
    admin_token: Option<String>,
    queue_settings: QueueSettings,
    mode: SchedulerMode,
    reject_once: bool,
    trace: Option<Arc<telemetry::Trace>>,
}

impl GatewayConfig {
    /// Configure read-only native handlers with application-owned credentials.
    ///
    /// This path does not read the environment or require an upstream HTTP API.
    pub fn native(
        service_id: &str,
        operations: Vec<crate::Operation>,
        credentials: Vec<Credential>,
    ) -> Result<Self, ConfigError> {
        let service = service::Service::native(service_id, operations)?;
        let identities = identities(credentials, &service.operations)?;
        Ok(Self {
            service,
            identities,
            upstream_url: None,
            admin_token: None,
            queue_settings: QueueSettings::default(),
            mode: SchedulerMode::default(),
            reject_once: false,
            trace: None,
        })
    }

    /// Parse the existing service manifest and resolve its token environment names.
    ///
    /// The resolver can read an application-owned credential store instead of the
    /// process environment. Nothing is read from the environment by this method.
    pub fn from_manifest(
        manifest: &str,
        upstream_url: &str,
        credentials: impl FnMut(&str) -> Result<String, std::env::VarError>,
    ) -> Result<Self, ConfigError> {
        Self::configured(
            service::Service::parse(manifest)?,
            upstream_url,
            credentials,
        )
    }

    fn configured(
        service: service::Service,
        upstream_url: &str,
        credentials: impl FnMut(&str) -> Result<String, std::env::VarError>,
    ) -> Result<Self, ConfigError> {
        let identities = service.identities(credentials)?;
        let upstream_url = service::upstream_origin(upstream_url.to_owned())
            .map_err(|error| ConfigError(error.into()))?;
        Ok(Self {
            service,
            identities,
            upstream_url: Some(upstream_url),
            admin_token: None,
            queue_settings: QueueSettings::default(),
            mode: SchedulerMode::default(),
            reject_once: false,
            trace: None,
        })
    }

    /// Load the standalone gateway's existing environment configuration.
    ///
    /// Preserves the built-in catalog default, manifest/token settings, optional
    /// admin token, queue deadlines, scheduler mode, and benchmark diagnostics.
    /// The caller remains responsible for binding its HTTP listener.
    pub fn from_env() -> Result<Self, ConfigError> {
        let service = service::Service::from_env()?;
        let upstream_url = match std::env::var("UPSTREAM_URL") {
            Ok(value) => value,
            Err(_) => {
                let port = match std::env::var("CATALOG_PORT") {
                    Ok(value) => value.parse::<u16>().ok().filter(|port| *port > 0),
                    Err(std::env::VarError::NotPresent) => Some(4000),
                    Err(_) => None,
                }
                .ok_or_else(|| ConfigError("CATALOG_PORT must be a port from 1 to 65535".into()))?;
                format!("http://127.0.0.1:{port}")
            }
        };
        if service.service_id != "catalog" && std::env::var_os("UPSTREAM_URL").is_none() {
            return Err(ConfigError("set UPSTREAM_URL for a custom service".into()));
        }
        let mut config = Self::configured(service, &upstream_url, |name| std::env::var(name))?;
        config.admin_token =
            crate::admin_token_from(std::env::var("ADMIN_TOKEN"), &config.identities)?;
        config.queue_settings = QueueSettings::from_env()?;
        config.mode = SchedulerMode::from_env()?;
        config.reject_once = std::env::var_os("TEST_429").is_some();
        config.trace = telemetry::Trace::from_env().map_err(|_| {
            ConfigError("Cannot create BENCHMARK_TRACE_PATH (must be a new file)".into())
        })?;
        Ok(config)
    }

    /// Enable the separate administrative endpoint using a distinct token.
    pub fn with_admin_token(mut self, token: String) -> Result<Self, ConfigError> {
        self.admin_token = crate::admin_token_from(Ok(token), &self.identities)?;
        Ok(self)
    }

    /// Set waiting deadlines, each from one millisecond through one hour.
    pub fn with_queue_settings(mut self, settings: QueueSettings) -> Result<Self, ConfigError> {
        for wait in [settings.interactive, settings.agent] {
            if !(Duration::from_millis(1)..=Duration::from_secs(3600)).contains(&wait)
                || wait.subsec_nanos() % 1_000_000 != 0
            {
                return Err(ConfigError(
                    "Queue waits must be whole milliseconds from 1 to 3600000".into(),
                ));
            }
        }
        self.queue_settings = settings;
        Ok(self)
    }

    /// Choose weighted selection (the default) or global admission-order FIFO.
    pub fn with_scheduler_mode(mut self, mode: SchedulerMode) -> Self {
        self.mode = mode;
        self
    }
}

/// A running gateway and the background tasks owned by it.
///
/// Keep this value alive while serving its router. Dropping it aborts its
/// scheduler and diagnostics task; it does not prove upstream execution stopped.
#[must_use = "Keep the gateway alive while serving its router"]
pub struct Gateway {
    state: Arc<AppState>,
    runtime: tokio::runtime::Handle,
    scheduler: JoinHandle<()>,
    sampler: Option<JoinHandle<()>>,
}

impl Gateway {
    /// Start one scheduler on the current Tokio runtime without binding a socket.
    /// Returns an error if no Tokio runtime is active or HTTP client initialization fails.
    pub async fn start(config: GatewayConfig) -> Result<Self, ConfigError> {
        let runtime = tokio::runtime::Handle::try_current()
            .map_err(|_| ConfigError("Gateway::start requires an active Tokio runtime".into()))?;
        let client = crate::upstream_client()?;
        let mut state = AppState::with_service(
            config.service,
            config.identities,
            config.reject_once,
            config.admin_token,
            config.queue_settings,
        );
        Arc::get_mut(&mut state).unwrap().trace = config.trace;
        state.queues.lock().await.mode = config.mode;
        let sampler = state
            .trace
            .as_ref()
            .map(|_| tokio::spawn(telemetry::sample(state.clone())));
        let scheduler = tokio::spawn(crate::scheduler_with_client(
            state.clone(),
            config.upstream_url,
            client,
        ));
        Ok(Self {
            state,
            runtime,
            scheduler,
            sampler,
        })
    }

    /// Return routes sharing this gateway's identities, queues, and budgets.
    ///
    /// Repeated calls do not create additional schedulers or independent budgets.
    /// Merge these routes into your application's Axum router.
    pub fn router(&self) -> axum::Router {
        app(self.state.clone())
    }

    /// Change an existing agent owner's ceiling, preserving accepted work.
    ///
    /// This is the in-process equivalent of the administrative HTTP update. It
    /// requires no admin token because the host application already owns this
    /// gateway. Unknown agent owners and limits outside 1..=1000 return an error.
    pub fn set_agent_limit(
        &self,
        principal_id: &str,
        maximum: usize,
    ) -> Result<crate::BudgetSnapshot, ConfigError> {
        if !(1..=1000).contains(&maximum) {
            return Err(ConfigError(
                "max_outstanding must be an integer from 1 to 1000".into(),
            ));
        }
        let budget = self
            .state
            .agent_budget(principal_id)
            .ok_or_else(|| ConfigError("Unknown agent principal".into()))?;
        Ok(budget.set_maximum(maximum))
    }

    /// Stop admitting new work while allowing accepted jobs to finish or expire.
    ///
    /// The admission lock orders closure with concurrent submissions. Discovery
    /// and administrative routes remain available to the host until it stops HTTP.
    pub async fn close(&self) {
        let _queues = self.state.queues.lock().await;
        self.state
            .closing
            .store(true, std::sync::atomic::Ordering::Relaxed);
        self.state.notify.notify_one();
    }

    /// Close admission, drain accepted work, and stop owned background tasks.
    ///
    /// Accepted queued work retains its original waiting deadline. Running work
    /// retains its permit. Native handlers must finish for draining to complete;
    /// callers can bound this future with `tokio::time::timeout`. Cancellation of
    /// this future drops the gateway and performs abrupt local cleanup instead.
    /// A scheduler task failure is returned as `JoinError`.
    pub async fn shutdown(mut self) -> Result<(), tokio::task::JoinError> {
        self.close().await;
        (&mut self.scheduler).await?;
        if let Some(sampler) = self.sampler.take() {
            sampler.abort();
            let _ = sampler.await;
        }
        Ok(())
    }
}

impl Drop for Gateway {
    fn drop(&mut self) {
        self.state
            .closing
            .store(true, std::sync::atomic::Ordering::Relaxed);
        self.scheduler.abort();
        if let Some(sampler) = &self.sampler {
            sampler.abort();
        }
        // A retained router must not keep queued reply channels and permits alive
        // after its owner disappears. Critical sections contain no I/O or awaits.
        if let Ok(mut queues) = self.state.queues.try_lock() {
            queues.reject_pending();
        } else {
            let state = self.state.clone();
            self.runtime
                .spawn(async move { state.queues.lock().await.reject_pending() });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Job, TaskAction};

    #[tokio::test]
    async fn drop_outside_runtime_cleans_pending_work_when_the_queue_lock_is_busy() {
        let config = GatewayConfig::from_manifest(
            include_str!("../services/catalog.json"),
            "http://127.0.0.1:1",
            |name| Ok(format!("test-{name}")),
        )
        .unwrap();
        let gateway = Gateway::start(config).await.unwrap();
        let state = gateway.state.clone();
        let budget = state.identities["test-AGENT_TOKEN_1"].budget.clone();
        let mut queues = state.queues.lock().await;
        let (reply, receiver) = tokio::sync::oneshot::channel();
        queues.agents.push_back(Job {
            reply,
            action: TaskAction::GetProduct(1),
            permit: budget.try_acquire().unwrap(),
            accepted_at: tokio::time::Instant::now(),
            max_wait: Duration::from_secs(10),
            order: 0,
        });
        std::thread::spawn(move || drop(gateway)).join().unwrap();
        drop(queues);
        let response = tokio::time::timeout(Duration::from_secs(3), receiver)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(response.status(), 503);
        assert_eq!(budget.snapshot().outstanding, 0);
        assert!(state.queues.lock().await.agents.is_empty());
    }
}
