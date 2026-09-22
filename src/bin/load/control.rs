use std::sync::{Arc, Mutex};
use tokio::sync::watch;

#[derive(serde::Deserialize)]
pub(super) struct Policy {
    pub version: u32,
    pub principal_id: String,
    pub client_class: String,
    pub policy_revision: u64,
    pub refresh_after_ms: u64,
    pub outstanding: usize,
    pub limits: Limits,
}

#[derive(serde::Deserialize)]
pub(super) struct Limits {
    pub scope: String,
    pub max_outstanding: usize,
}

#[derive(Clone, Default)]
pub(super) struct State {
    pub principal: Option<String>,
    pub revision: u64,
    pub maximum: usize,
    pub active: usize,
    pub server_outstanding: usize,
    pub ready: bool,
    pub stopped: bool,
}

pub(super) struct Gate {
    state: Mutex<State>,
    changed: watch::Sender<()>,
}

impl Gate {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(State::default()),
            changed: watch::channel(()).0,
        })
    }

    pub fn snapshot(&self) -> State {
        self.state.lock().unwrap().clone()
    }

    pub fn subscribe(&self) -> watch::Receiver<()> {
        self.changed.subscribe()
    }

    pub fn apply(&self, policy: Policy) -> Result<(), &'static str> {
        if policy.version != 3
            || policy.client_class != "agent"
            || policy.principal_id.is_empty()
            || policy.limits.scope != "principal"
            || !(1..=1000).contains(&policy.limits.max_outstanding)
            || policy.policy_revision == 0
            || !(100..=60_000).contains(&policy.refresh_after_ms)
        {
            return Err("Invalid or unsupported dynamic policy");
        }
        let mut state = self.state.lock().unwrap();
        if state
            .principal
            .as_ref()
            .is_some_and(|principal| principal != &policy.principal_id)
            || policy.policy_revision < state.revision
            || (policy.policy_revision == state.revision
                && policy.limits.max_outstanding != state.maximum)
        {
            return Err("Inconsistent policy revision or principal");
        }
        state.principal = Some(policy.principal_id);
        state.revision = policy.policy_revision;
        state.maximum = policy.limits.max_outstanding;
        state.server_outstanding = policy.outstanding;
        state.ready = true;
        drop(state);
        self.changed.send_replace(());
        Ok(())
    }

    pub fn pause(&self) {
        self.state.lock().unwrap().ready = false;
        self.changed.send_replace(());
    }

    pub fn stop(&self) {
        self.state.lock().unwrap().stopped = true;
        self.changed.send_replace(());
    }

    pub async fn acquire(self: &Arc<Self>) -> Result<Attempt, &'static str> {
        // Subscribe before checking state so a resize/release cannot be missed.
        let mut changed = self.subscribe();
        loop {
            {
                let mut state = self.state.lock().unwrap();
                if state.stopped {
                    return Err("Client stopped");
                }
                if state.ready && state.active < state.maximum {
                    state.active += 1;
                    drop(state);
                    self.changed.send_replace(());
                    return Ok(Attempt(self.clone()));
                }
            }
            changed.changed().await.map_err(|_| "Client stopped")?;
        }
    }
}

pub(super) struct Attempt(Arc<Gate>);

impl Drop for Attempt {
    fn drop(&mut self) {
        self.0.state.lock().unwrap().active -= 1;
        self.0.changed.send_replace(());
    }
}
