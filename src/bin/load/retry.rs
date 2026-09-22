use reqwest::StatusCode;
use std::future::Future;
use std::time::Duration;
use tokio::time::sleep;

pub(super) struct Response {
    pub status: StatusCode,
    pub retry_after: Option<String>,
    pub body: Vec<u8>,
}

fn safe_to_retry(response: &Response) -> bool {
    if response.status == StatusCode::TOO_MANY_REQUESTS {
        return true;
    }
    if response.status != StatusCode::SERVICE_UNAVAILABLE {
        return false;
    }
    let Ok(body) = serde_json::from_slice::<serde_json::Value>(&response.body) else {
        return false;
    };
    body["error"]["code"] == "queue_timeout" && body["error"]["execution"] == "not_started"
}

pub(super) async fn run<F, Fut>(mut attempt: F) -> Result<(), String>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<Response, String>>,
{
    for number in 1..=5 {
        let response = attempt().await?;
        if response.status.is_success() {
            return Ok(());
        }
        if number == 5 || !safe_to_retry(&response) {
            return Err(format!(
                "Operation HTTP status {}",
                response.status.as_u16()
            ));
        }
        let raw = response.retry_after.as_deref().unwrap_or("1").trim();
        let seconds = if !raw.is_empty() && raw.bytes().all(|byte| byte.is_ascii_digit()) {
            raw.parse::<u64>()
                .map_err(|_| "Retry-After exceeds the client deadline")?
        } else {
            1
        };
        if seconds > 86400 {
            return Err("Retry-After exceeds the client deadline".into());
        }
        // The caller's overall workload deadline also bounds retry waiting.
        sleep(Duration::from_secs(seconds)).await;
    }
    unreachable!()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };
    use tokio::time::{Instant, advance};

    #[tokio::test(start_paused = true)]
    async fn only_documented_timeouts_and_429_share_five_attempts_and_wait() {
        let calls = Arc::new(AtomicUsize::new(0));
        let count = calls.clone();
        let start = Instant::now();
        let task = tokio::spawn(run(move || {
            let number = count.fetch_add(1, Ordering::SeqCst);
            async move {
                Ok(Response {
                    status: if number % 2 == 0 {
                        StatusCode::TOO_MANY_REQUESTS
                    } else {
                        StatusCode::SERVICE_UNAVAILABLE
                    },
                    retry_after: Some("2".into()),
                    body: br#"{"error":{"code":"queue_timeout","execution":"not_started"}}"#
                        .to_vec(),
                })
            }
        }));
        tokio::task::yield_now().await;
        for expected in 1..5 {
            assert_eq!(calls.load(Ordering::SeqCst), expected);
            advance(Duration::from_millis(1999)).await;
            assert_eq!(calls.load(Ordering::SeqCst), expected);
            advance(Duration::from_millis(1)).await;
            tokio::task::yield_now().await;
        }
        assert!(task.await.unwrap().is_err());
        assert_eq!(calls.load(Ordering::SeqCst), 5);
        assert_eq!(start.elapsed(), Duration::from_secs(8));
    }

    #[tokio::test(start_paused = true)]
    async fn unsafe_errors_never_retry_and_deadline_bounds_safe_retries() {
        for (status, body) in [
            (
                503,
                r#"{"error":{"code":"queue_timeout","execution":"started"}}"#,
            ),
            (
                503,
                r#"{"error":{"code":"upstream_timeout","execution":"not_started"}}"#,
            ),
            (503, r#"{"error":{"code":"queue_timeout"}}"#),
            (503, "not JSON"),
            (503, "[]"),
            (
                502,
                r#"{"error":{"code":"queue_timeout","execution":"not_started"}}"#,
            ),
            (504, "{}"),
            (401, "{}"),
            (403, "{}"),
        ] {
            let mut calls = 0;
            assert!(
                run(|| {
                    calls += 1;
                    async move {
                        Ok(Response {
                            status: StatusCode::from_u16(status).unwrap(),
                            retry_after: Some("1".into()),
                            body: body.as_bytes().to_vec(),
                        })
                    }
                })
                .await
                .is_err()
            );
            assert_eq!(calls, 1);
        }
        let mut calls = 0;
        let outcome = tokio::time::timeout(
            Duration::from_secs(3),
            run(|| {
                calls += 1;
                async {
                    Ok(Response {
                        status: StatusCode::SERVICE_UNAVAILABLE,
                        retry_after: Some("10".into()),
                        body: br#"{"error":{"code":"queue_timeout","execution":"not_started"}}"#
                            .to_vec(),
                    })
                }
            }),
        )
        .await;
        assert!(outcome.is_err());
        assert_eq!(calls, 1);
    }
}
