//! One submission per connection. Never retry a lost or ambiguous local reply.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::time::{Instant, timeout_at};

pub const MAX_FRAME: u64 = 64 * 1024;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub id: String,
    pub token: String,
    pub operation: String,
    pub params: Value,
    pub deadline_unix_ms: f64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct Reply {
    pub ok: bool,
    pub code: String,
    pub execution: String,
    pub attempts: Option<usize>,
    pub status: Option<u16>,
    pub body: Option<Value>,
}
impl Reply {
    pub fn error(code: &str, execution: &str, attempts: usize) -> Self {
        Self {
            ok: false,
            code: code.into(),
            execution: execution.into(),
            attempts: Some(attempts),
            status: None,
            body: None,
        }
    }
}

pub async fn submit(socket: &str, request: &Request, deadline: Instant) -> Reply {
    let mut stream = match timeout_at(deadline, UnixStream::connect(socket)).await {
        Ok(Ok(stream)) => stream,
        _ => return Reply::error("dispatcher_unavailable", "not_started", 0),
    };
    let mut bytes = match serde_json::to_vec(request) {
        Ok(bytes) if bytes.len() < MAX_FRAME as usize => bytes,
        _ => return Reply::error("invalid_dispatch_request", "not_started", 0),
    };
    bytes.push(b'\n');
    let exchange = async {
        stream.write_all(&bytes).await.ok()?;
        let mut reader = BufReader::new(stream).take(MAX_FRAME + 1);
        let mut response = Vec::new();
        reader.read_until(b'\n', &mut response).await.ok()?;
        if response.len() > MAX_FRAME as usize || !response.ends_with(b"\n") {
            return None;
        }
        serde_json::from_slice(&response).ok()
    };
    match timeout_at(deadline, exchange).await {
        Ok(Some(reply)) => reply,
        _ => {
            let mut reply = Reply::error("dispatcher_response_lost", "unknown", 0);
            reply.attempts = None;
            reply
        }
    }
}
