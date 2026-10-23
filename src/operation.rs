use crate::{ConfigError, service};
use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::{future::Future, pin::Pin, sync::Arc};

pub(crate) type Execution = Pin<Box<dyn Future<Output = Response> + Send>>;
type Prepare = dyn Fn(Value) -> Result<Execution, Response> + Send + Sync;

/// A read-only operation executed inside the host application's Tokio runtime.
///
/// Input is deserialized before admission. The handler runs only after scheduler
/// selection, sharing the same budget and queue rules as mapped HTTP operations.
pub struct Operation {
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) path: String,
    pub(crate) input_schema: Value,
    prepare: Arc<Prepare>,
}

impl Operation {
    /// Register a typed asynchronous handler and its discovery schema.
    ///
    /// `P` defines deserialization validation, including `deny_unknown_fields`
    /// and custom Serde validation. The application must keep `input_schema`
    /// consistent with `P`; this method is not a JSON Schema validation engine.
    /// Schema constraints not expressed by `P` must be validated by the application.
    /// Handlers must be asynchronous, nonblocking, and read-only. Their returned
    /// future is not polled until the job is selected for execution.
    pub fn new<P, F, Fut, R>(
        name: &str,
        description: &str,
        path: &str,
        input_schema: Value,
        handler: F,
    ) -> Result<Self, ConfigError>
    where
        P: DeserializeOwned + Send + 'static,
        F: Fn(P) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = R> + Send + 'static,
        R: IntoResponse + 'static,
    {
        if !service::identifier(name) || !service::operation_path(path) {
            return Err(ConfigError(
                "Invalid operation name or reserved path".into(),
            ));
        }
        if !input_schema.is_object() {
            return Err(ConfigError(
                "Operation input_schema must be an object".into(),
            ));
        }
        let handler = Arc::new(handler);
        Ok(Self {
            name: name.into(),
            description: description.into(),
            path: path.into(),
            input_schema,
            prepare: Arc::new(move |params| {
                let params = serde_json::from_value::<P>(params).map_err(|_| {
                    (
                        StatusCode::UNPROCESSABLE_ENTITY,
                        "Invalid operation parameters",
                    )
                        .into_response()
                })?;
                let handler = handler.clone();
                Ok(Box::pin(
                    async move { handler(params).await.into_response() },
                ))
            }),
        })
    }

    pub(crate) fn prepare(&self, params: Value) -> Result<Execution, Response> {
        (self.prepare)(params)
    }
}
