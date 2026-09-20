use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{oneshot, Mutex, Notify};
use tokio::task::JoinSet;
use std::sync::atomic::{AtomicBool, Ordering};

enum CatalogAction {
    Search(String),
    GetProduct(u64),
}

struct Job {
    reply: oneshot::Sender<Response>,
    action: CatalogAction,
}

struct Queues {
    humans: VecDeque<Job>,
    agents: VecDeque<Job>,
}

struct AppState {
    queues: Mutex<Queues>,
    notify: Notify,
    reject_once: AtomicBool,
}

#[derive(serde::Deserialize)]
struct ProductRequest {
    id: u64,
}

#[derive(serde::Deserialize)]
struct WorkRequest {
    query: String,
}

#[derive(serde::Serialize)]
struct Operation {
    name: &'static str,
    description: &'static str,
    method: &'static str,
    path: &'static str,
    input_schema: serde_json::Value,
}

#[derive(serde::Serialize)]
struct AgentPolicy {
    version: u32,
    max_in_flight: usize,
    operations: Vec<Operation>,
}

async fn agent_policy() -> Json<AgentPolicy> {
    Json(AgentPolicy {
        version: 2,
        max_in_flight: 10,
                operations: vec![
            Operation {
                name: "search_products",
                description: "Поиск товаров по подстроке в английском названии. \
                              Для кроссовок передай query=\"sneakers\", \
                              для ботинок — query=\"boots\". \
                              Чтобы получить все товары, передай query=\"\". \
                              Каталог содержит только обувь, поэтому запрос \
                              всей доступной обуви означает получение всех товаров.",
                method: "POST",
                path: "/search",
                input_schema: serde_json::json!({
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Строка для поиска товаров"
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": false
                }),
            },
            Operation {
                name: "get_product",
                description: "Получить один товар по известному числовому ID. \
                              Используй, когда пользователь указал ID товара.",
                method: "POST",
                path: "/product",
                input_schema: serde_json::json!({
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Идентификатор товара"
                        }
                    },
                    "required": ["id"],
                    "additionalProperties": false
                }),
            },
        ],
    })
}

#[tokio::main]
async fn main() {
    let state = Arc::new(AppState {
        queues: Mutex::new(Queues {
            humans: VecDeque::new(),
            agents: VecDeque::new(),
        }),
        notify: Notify::new(),
        reject_once: AtomicBool::new(
            std::env::var_os("TEST_429").is_some()
        ),
    });

    // The scheduler runs as a separate asynchronous task.
    tokio::spawn(scheduler(state.clone()));

    let app = Router::new()
        .route("/agent-policy", get(agent_policy))
        .route("/search", post(work))
        .route("/product", post(get_product))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:3000")
        .await
        .unwrap();

    println!("Сервер запущен на порту 3000");

    axum::serve(listener, app).await.unwrap();
}

async fn enqueue(
    state: Arc<AppState>,
    headers: HeaderMap,
    action: CatalogAction,
) -> Response {
    let is_agent = match headers.get("X-Client-Type") {
        Some(value) => value == "agent",
        None => false,
    };

    if is_agent && state.reject_once.swap(false, Ordering::Relaxed) {
        println!("Тест: отклоняем один агентный запрос до выполнения");

        return (
            StatusCode::TOO_MANY_REQUESTS,
            [("Retry-After", "2")],
            "Test rejection",
        )
            .into_response();
    }

    let (sender, receiver) = oneshot::channel();
    let job = Job {
        reply: sender,
        action,
    };

    {
        let mut queues = state.queues.lock().await;

        if is_agent {
            // Allow at most 32 queued agent tasks.
            if queues.agents.len() >= 32 {
                return (
                    StatusCode::TOO_MANY_REQUESTS,
                    [("Retry-After", "1")],
                    "Agent queue is full",
                )
                    .into_response();
            }

            queues.agents.push_back(job);
        } else {
            queues.humans.push_back(job);
        }
    }

    state.notify.notify_one();

    match receiver.await {
        Ok(response) => response,
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

async fn work(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(request): Json<WorkRequest>,
) -> Response {
    enqueue(
        state,
        headers,
        CatalogAction::Search(request.query),
    )
    .await
}

async fn get_product(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(request): Json<ProductRequest>,
) -> Response {
    enqueue(
        state,
        headers,
        CatalogAction::GetProduct(request.id),
    )
    .await
}

async fn scheduler(state: Arc<AppState>) {
    let mut running = JoinSet::<()>::new();
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();

    loop {
        // Wake up when a new request arrives or a task completes.
        tokio::select! {
            _ = state.notify.notified() => {}
            Some(result) = running.join_next(),
                if !running.is_empty() => {
                result.unwrap();
            }
        }

        // Fill the available slots.
        while running.len() < 10 {
            let next = {
                let mut queues = state.queues.lock().await;

                // Give human requests priority when selecting the next job.
                if let Some(job) = queues.humans.pop_front() {
                    Some(job)
                } else {
                    queues.agents.pop_front()
                }
            };

            let job = match next {
                Some(job) => job,
                None => break,
            };

            // Skip the request if its handler has already been canceled.
            if job.reply.is_closed() {
                continue;
            }

            let client = client.clone();

            running.spawn(async move {
                let response = call_catalog(client, job.action).await;

                let _ = job.reply.send(response);
            });
        }
    }
}

async fn call_catalog(
    client: reqwest::Client,
    action: CatalogAction,
) -> Response {
    let request = match action {
        CatalogAction::Search(query) => {
            client
                .get("http://127.0.0.1:4000/products/search")
                .query(&[("query", query)])
        }
        CatalogAction::GetProduct(id) => {
            client
                .get("http://127.0.0.1:4000/products/get")
                .query(&[("id", id)])
        }
    };

    let result = request.send().await;

    match result {
        Ok(response) => {
            // Ошибка каталога — ошибка обращения к нижележащему сервису.
            if response.status() != reqwest::StatusCode::OK {
                return (
                    StatusCode::BAD_GATEWAY,
                    "Catalog returned an error",
                )
                    .into_response();
            }

            match response.bytes().await {
                Ok(body) => (
                    StatusCode::OK,
                    [("Content-Type", "application/json")],
                    body,
                )
                    .into_response(),

                Err(_) => (
                    StatusCode::BAD_GATEWAY,
                    "Cannot read catalog response",
                )
                    .into_response(),
            }
        }

        Err(error) => {
            if error.is_timeout() {
                (
                    StatusCode::GATEWAY_TIMEOUT,
                    "Catalog timeout",
                )
                    .into_response()
            } else {
                (
                    StatusCode::BAD_GATEWAY,
                    "Catalog unavailable",
                )
                    .into_response()
            }
        }
    }
}