use std::sync::{Arc, Mutex};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Snapshot {
    pub maximum: usize,
    pub outstanding: usize,
    pub revision: u64,
}

pub(crate) struct Budget {
    state: Mutex<Snapshot>,
}

impl Budget {
    pub fn new(maximum: usize) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(Snapshot {
                maximum,
                outstanding: 0,
                revision: 1,
            }),
        })
    }

    pub fn snapshot(&self) -> Snapshot {
        *self.state.lock().unwrap()
    }

    pub fn set_maximum(&self, maximum: usize) -> Snapshot {
        let mut state = self.state.lock().unwrap();
        if state.maximum != maximum {
            state.maximum = maximum;
            state.revision += 1;
        }
        *state
    }

    #[cfg(test)]
    pub fn try_acquire(self: &Arc<Self>) -> Option<Permit> {
        self.try_acquire_observed().map(|(permit, _)| permit)
    }

    // The snapshot belongs to the admission linearization point, even if an
    // administrator resizes the budget before telemetry records the event.
    pub fn try_acquire_observed(self: &Arc<Self>) -> Option<(Permit, Snapshot)> {
        let mut state = self.state.lock().unwrap();
        if state.outstanding >= state.maximum {
            return None;
        }
        state.outstanding += 1;
        Some((Permit(self.clone()), *state))
    }

    #[cfg(test)]
    pub fn available_permits(&self) -> usize {
        let state = self.snapshot();
        state.maximum.saturating_sub(state.outstanding)
    }
}

// A job keeps this guard through queueing, execution, and response-body reads.
// Resizing changes the same state that every existing guard releases into.
pub(crate) struct Permit(Arc<Budget>);

impl Drop for Permit {
    fn drop(&mut self) {
        let mut state = self.0.state.lock().unwrap();
        state.outstanding -= 1;
    }
}
