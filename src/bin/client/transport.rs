//! Shared authenticated HTTP transport; errors never contain credentials or URLs.
use super::{control::Policy, retry::Response};
use reqwest::Client;
use serde_json::Value;
use std::time::Duration;

#[allow(dead_code)]
pub async fn policy(http: &Client, base: &str, token: &str) -> Result<Policy, &'static str> {
    discovery(http, base, token).await
}

pub async fn discovery<T: serde::de::DeserializeOwned>(
    http: &Client,
    base: &str,
    token: &str,
) -> Result<T, &'static str> {
    http.get(format!("{base}/agent-policy"))
        .bearer_auth(token)
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .map_err(|_| "Policy transport error")?
        .error_for_status()
        .map_err(|_| "Policy HTTP error")?
        .json()
        .await
        .map_err(|_| "Invalid policy JSON")
}

pub async fn send(
    http: &Client,
    base: &str,
    token: &str,
    operation: &str,
    params: &Value,
) -> Result<Response, String> {
    let response = http
        .post(format!("{base}/{operation}"))
        .bearer_auth(token)
        .json(params)
        .send()
        .await
        .map_err(|_| "network_error".to_string())?;
    let status = response.status();
    let retry_after = response
        .headers()
        .get("Retry-After")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    let body = response
        .bytes()
        .await
        .map_err(|_| "network_error".to_string())?
        .to_vec();
    Ok(Response {
        status,
        retry_after,
        body,
    })
}
