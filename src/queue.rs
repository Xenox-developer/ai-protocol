use super::{ClientClass, Job, queue_timeout};
use std::collections::VecDeque;
use std::time::Duration;
use tokio::time::Instant;

#[derive(Clone, Copy)]
pub(crate) struct QueueSettings {
    pub interactive: Duration,
    pub agent: Duration,
}

impl Default for QueueSettings {
    fn default() -> Self {
        Self {
            interactive: Duration::from_millis(2000),
            agent: Duration::from_millis(10000),
        }
    }
}

impl QueueSettings {
    pub fn for_class(self, class: ClientClass) -> Duration {
        match class {
            ClientClass::Interactive => self.interactive,
            ClientClass::Agent => self.agent,
        }
    }

    pub fn from_env() -> Result<Self, String> {
        let read = |name: &str, default: u64| {
            let value = match std::env::var(name) {
                Ok(value) => value.parse::<u64>().ok(),
                Err(std::env::VarError::NotPresent) => Some(default),
                Err(_) => None,
            };
            value
                .filter(|value| (1..=3_600_000).contains(value))
                .map(Duration::from_millis)
                .ok_or_else(|| format!("{name} must be an integer from 1 to 3600000"))
        };
        Ok(Self {
            interactive: read("INTERACTIVE_MAX_WAIT_MS", 2000)?,
            agent: read("AGENT_MAX_WAIT_MS", 10000)?,
        })
    }
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub(crate) enum SchedulerMode {
    #[default]
    Weighted,
    Fifo,
}

impl SchedulerMode {
    pub fn from_env() -> Result<Self, String> {
        match std::env::var("SCHEDULER_MODE").as_deref() {
            Ok("weighted") | Err(std::env::VarError::NotPresent) => Ok(Self::Weighted),
            Ok("fifo") => Ok(Self::Fifo),
            _ => Err("SCHEDULER_MODE must be weighted or fifo".into()),
        }
    }
}

#[derive(Default)]
pub(crate) struct Queues {
    pub interactive: VecDeque<Job>,
    pub agents: VecDeque<Job>,
    interactive_streak: u8,
    pub mode: SchedulerMode,
    next_order: u64,
}

impl Queues {
    pub fn take_order(&mut self) -> u64 {
        let order = self.next_order;
        self.next_order = order.checked_add(1).expect("Admission sequence exhausted");
        order
    }

    #[cfg(test)]
    pub fn prune_cancelled(&mut self) {
        self.interactive.retain(|job| !job.reply.is_closed());
        self.agents.retain(|job| !job.reply.is_closed());
    }

    pub fn maintain(&mut self, now: Instant) {
        for queue in [&mut self.interactive, &mut self.agents] {
            // Scan the bounded queues without allocating or changing survivor order.
            for _ in 0..queue.len() {
                let job = queue.pop_front().unwrap();
                if job.reply.is_closed() {
                    continue;
                }
                if job.deadline() <= now {
                    let Job { reply, permit, .. } = job;
                    drop(permit);
                    let _ = reply.send(queue_timeout());
                } else {
                    queue.push_back(job);
                }
            }
        }
    }

    pub fn pop_next(&mut self) -> Option<Job> {
        loop {
            self.maintain(Instant::now());
            let competing = !self.interactive.is_empty() && !self.agents.is_empty();
            let interactive = if self.mode == SchedulerMode::Fifo {
                match (self.interactive.front(), self.agents.front()) {
                    (Some(left), Some(right)) => left.order < right.order,
                    (Some(_), None) => true,
                    _ => false,
                }
            } else {
                !self.interactive.is_empty()
                    && (self.agents.is_empty() || self.interactive_streak < 3)
            };
            let selected = if interactive {
                self.interactive.pop_front()
            } else {
                self.agents.pop_front()
            };
            let Some(job) = selected else {
                self.interactive_streak = 0;
                return None;
            };
            if job.reply.is_closed() {
                continue;
            }
            // Recheck at handoff, after scanning the queues. The guard remains
            // owned here: the executor and timeout can never both own this job.
            if job.deadline() <= Instant::now() {
                let Job { reply, permit, .. } = job;
                drop(permit);
                let _ = reply.send(queue_timeout());
                continue;
            }
            self.interactive_streak =
                if self.mode == SchedulerMode::Weighted && competing && interactive {
                    self.interactive_streak + 1
                } else {
                    0
                };
            return Some(job);
        }
    }

    pub fn next_deadline(&self) -> Option<Instant> {
        self.interactive
            .iter()
            .chain(self.agents.iter())
            .map(Job::deadline)
            .min()
    }
}
