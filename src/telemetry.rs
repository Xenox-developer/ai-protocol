//! Optional local benchmark diagnostics. No credentials or operation bodies.
use super::AppState;
use serde_json::{Value, json};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub(crate) struct Trace(Mutex<File>);

impl Trace {
    pub fn from_env() -> std::io::Result<Option<Arc<Self>>> {
        let Some(path) = std::env::var_os("BENCHMARK_TRACE_PATH") else {
            return Ok(None);
        };
        Ok(Some(Arc::new(Self(Mutex::new(
            OpenOptions::new().write(true).create_new(true).open(path)?,
        )))))
    }

    pub fn emit(&self, mut value: Value) {
        value["unix_s"] = json!(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs_f64()
        );
        let mut file = self.0.lock().unwrap();
        // Fail visibly rather than silently losing benchmark measurements.
        serde_json::to_writer(&mut *file, &value).expect("Cannot write benchmark trace");
        writeln!(file).expect("Cannot write benchmark trace");
    }
}

pub(crate) struct Active(Arc<AtomicUsize>);
impl Active {
    pub fn new(count: Arc<AtomicUsize>) -> Self {
        count.fetch_add(1, Ordering::Relaxed);
        Self(count)
    }
}
impl Drop for Active {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}

pub(crate) async fn sample(state: Arc<AppState>) {
    let mut timer = tokio::time::interval(Duration::from_millis(50));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        timer.tick().await;
        let queues = state.queues.lock().await;
        let mut budgets = std::collections::BTreeMap::new();
        for identity in state.identities.values() {
            let budget = identity.budget.snapshot();
            let class = serde_json::to_value(identity.class).unwrap();
            budgets.insert(format!("{}:{}", identity.principal_id, class.as_str().unwrap()), json!({
                "principal_id": identity.principal_id, "class": class,
                "limit": budget.maximum, "outstanding": budget.outstanding, "revision": budget.revision
            }));
        }
        state.trace.as_ref().unwrap().emit(json!({
            "event": "sample", "queue_interactive": queues.interactive.len(),
            "queue_agent": queues.agents.len(), "active": state.active.load(Ordering::Relaxed),
            "budgets": budgets.into_values().collect::<Vec<_>>()
        }));
    }
}
