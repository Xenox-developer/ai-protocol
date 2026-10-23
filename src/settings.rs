use crate::ConfigError;
use std::time::Duration;

/// Per-process resource bounds. Owner budgets remain independent of these bounds.
#[derive(Clone, Copy, Debug)]
pub struct RuntimeSettings {
    /// Maximum concurrently executing operations (1..=1000).
    pub execution_limit: usize,
    /// Maximum waiting jobs in each scheduling class (1..=10000).
    pub queue_capacity: usize,
    /// Deadline covering execution and materialization of the response body.
    pub execution_timeout: Duration,
    /// Maximum buffered response size, in bytes (1..=64 MiB).
    pub max_response_bytes: usize,
    /// Maximum incoming operation JSON size, in bytes (1..=64 MiB).
    pub max_request_bytes: usize,
}

impl Default for RuntimeSettings {
    fn default() -> Self {
        Self {
            execution_limit: 10,
            queue_capacity: 32,
            execution_timeout: Duration::from_secs(5),
            max_response_bytes: 16 * 1024 * 1024,
            max_request_bytes: 2 * 1024 * 1024,
        }
    }
}

impl RuntimeSettings {
    pub(crate) fn validate(self) -> Result<Self, ConfigError> {
        if !(1..=1000).contains(&self.execution_limit)
            || !(1..=10000).contains(&self.queue_capacity)
            || !(Duration::from_millis(1)..=Duration::from_secs(3600))
                .contains(&self.execution_timeout)
            || self.execution_timeout.subsec_nanos() % 1_000_000 != 0
            || !(1..=64 * 1024 * 1024).contains(&self.max_response_bytes)
            || !(1..=64 * 1024 * 1024).contains(&self.max_request_bytes)
        {
            return Err(ConfigError("Invalid runtime resource bounds".into()));
        }
        Ok(self)
    }

    pub(crate) fn from_env() -> Result<Self, ConfigError> {
        let default = Self::default();
        Self {
            execution_limit: number("GATEWAY_EXECUTORS", default.execution_limit)?,
            queue_capacity: number("GATEWAY_QUEUE_CAPACITY", default.queue_capacity)?,
            execution_timeout: Duration::from_millis(
                number("GATEWAY_EXECUTION_TIMEOUT_MS", 5000)? as u64
            ),
            max_response_bytes: number("GATEWAY_MAX_RESPONSE_BYTES", default.max_response_bytes)?,
            max_request_bytes: number("GATEWAY_MAX_REQUEST_BYTES", default.max_request_bytes)?,
        }
        .validate()
    }
}

pub(crate) fn number(name: &str, default: usize) -> Result<usize, ConfigError> {
    match std::env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|_| ConfigError(format!("{name} must be an unsigned integer"))),
        Err(std::env::VarError::NotPresent) => Ok(default),
        Err(_) => Err(ConfigError(format!("Invalid {name}"))),
    }
}
