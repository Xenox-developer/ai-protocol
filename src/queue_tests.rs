use super::*;
use tokio::time::{Instant, advance};

fn make_job(budget: &Arc<Budget>, id: u64, wait: Duration) -> (Job, oneshot::Receiver<Response>) {
    let (reply, receiver) = oneshot::channel();
    (
        Job {
            reply,
            action: TaskAction::GetProduct(id),
            permit: budget.try_acquire().unwrap(),
            accepted_at: Instant::now(),
            max_wait: wait,
            order: id,
        },
        receiver,
    )
}

fn id(job: &Job) -> u64 {
    match job.action {
        TaskAction::GetProduct(id) => id,
        _ => panic!("Expected a product task"),
    }
}

async fn assert_queue_timeout(response: Response) {
    assert_eq!(response.status(), 503);
    assert_eq!(response.headers()["retry-after"], "1");
    let bytes = axum::body::to_bytes(response.into_body(), 1024)
        .await
        .unwrap();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(
        body,
        serde_json::json!({"error":{"code":"queue_timeout","execution":"not_started"}})
    );
}

struct Executor {
    selected: mpsc::UnboundedReceiver<(u64, oneshot::Sender<()>)>,
    task: JoinHandle<()>,
}

impl Drop for Executor {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn executor(state: Arc<AppState>) -> Executor {
    let (tx, selected) = mpsc::unbounded_channel();
    let task = tokio::spawn(run_scheduler(state, move |action| {
        let TaskAction::GetProduct(id) = action else {
            panic!("Expected product task")
        };
        let (complete, finished) = oneshot::channel();
        // Observe handoff, rather than the nondeterministic order futures first run.
        tx.send((id, complete)).unwrap();
        async move {
            finished.await.unwrap();
            StatusCode::OK.into_response()
        }
    }));
    Executor { selected, task }
}

#[tokio::test(start_paused = true)]
async fn continuous_competition_selects_three_to_one_and_keeps_fifo() {
    let budget = Budget::new(100);
    let mut queues = Queues::default();
    let mut receivers = Vec::new();
    for index in 0..24 {
        let (job, rx) = make_job(&budget, index, Duration::from_secs(100));
        queues.interactive.push_back(job);
        receivers.push(rx);
    }
    for index in 100..108 {
        let (job, rx) = make_job(&budget, index, Duration::from_secs(100));
        queues.agents.push_back(job);
        receivers.push(rx);
    }
    for cycle in 0..8 {
        for offset in 0..3 {
            assert_eq!(id(&queues.pop_next().unwrap()), cycle * 3 + offset);
        }
        assert_eq!(id(&queues.pop_next().unwrap()), 100 + cycle);
    }
    assert!(queues.pop_next().is_none());
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test(start_paused = true)]
async fn single_class_uses_every_selection_without_accumulating_credits() {
    let budget = Budget::new(10);
    let mut queues = Queues::default();
    for agent in [false, true] {
        for index in 0..50 {
            let (job, _rx) = make_job(&budget, index, Duration::from_secs(100));
            if agent {
                queues.agents.push_back(job);
            } else {
                queues.interactive.push_back(job);
            }
            assert_eq!(id(&queues.pop_next().unwrap()), index);
        }
        let mut receivers = Vec::new();
        for index in 0..4 {
            let (job, rx) = make_job(&budget, index, Duration::from_secs(100));
            queues.interactive.push_back(job);
            receivers.push(rx);
        }
        let (job, rx) = make_job(&budget, 100, Duration::from_secs(100));
        queues.agents.push_back(job);
        receivers.push(rx);
        for expected in [0, 1, 2, 100, 3] {
            assert_eq!(id(&queues.pop_next().unwrap()), expected);
        }
    }
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test(start_paused = true)]
async fn scheduler_fills_ten_slots_and_reuses_each_completion_without_clock_ticks() {
    let state = test_state();
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    budget.set_maximum(20);
    let mut requests = Vec::new();
    for index in 0..12 {
        requests.push(submit(
            &state,
            "AGENT_TOKEN_1",
            TaskAction::GetProduct(index),
        ));
        slots(&budget, 19 - index as usize).await;
    }
    let before = Instant::now();
    let mut executor = executor(state.clone());
    let mut running = Vec::new();
    for expected in 0..10 {
        let (id, release) = executor.selected.recv().await.unwrap();
        assert_eq!(id, expected);
        running.push(release);
    }
    assert!(executor.selected.try_recv().is_err());
    assert_eq!(state.queues.lock().await.agents.len(), 2);
    for expected in 10..12 {
        running.remove(0).send(()).unwrap();
        let (id, release) = executor.selected.recv().await.unwrap();
        assert_eq!(id, expected);
        running.push(release);
    }
    assert_eq!(Instant::now(), before);
    for release in running {
        release.send(()).unwrap();
    }
    for request in requests {
        assert_eq!(finish(request).await.status(), 200);
    }
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test(start_paused = true)]
async fn timer_expires_waiters_without_events_and_preserves_running_jobs_and_resized_budget() {
    let state = test_state();
    let mut executor = executor(state.clone());
    let mut requests = Vec::new();
    let mut running = Vec::new();
    for index in 0..10 {
        requests.push(submit(
            &state,
            "INTERACTIVE_TOKEN",
            TaskAction::GetProduct(index),
        ));
    }
    for _ in 0..10 {
        running.push(executor.selected.recv().await.unwrap().1);
    }
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let mut expired = Vec::new();
    for index in 100..103 {
        expired.push(submit(
            &state,
            "AGENT_TOKEN_1",
            TaskAction::GetProduct(index),
        ));
    }
    slots(&budget, 2).await;
    budget.set_maximum(2);
    assert_eq!(budget.snapshot().outstanding, 3);
    assert_eq!(
        finish(submit(&state, "AGENT_TOKEN_2", TaskAction::GetProduct(200)))
            .await
            .status(),
        429
    );
    advance(Duration::from_millis(9999)).await;
    assert!(expired.iter().all(|task| !task.is_finished()));
    assert!(requests.iter().all(|task| !task.is_finished()));
    advance(Duration::from_millis(1)).await;
    for request in expired {
        assert_queue_timeout(finish(request).await).await;
    }
    assert_eq!(
        budget.snapshot(),
        budget::Snapshot {
            maximum: 2,
            outstanding: 0,
            revision: 2
        }
    );
    assert!(
        executor.selected.try_recv().is_err(),
        "Expired jobs must never reach the executor"
    );
    assert!(
        requests.iter().all(|task| !task.is_finished()),
        "Running jobs ignore queue deadlines"
    );
    for release in running {
        release.send(()).unwrap();
    }
    for request in requests {
        assert_eq!(finish(request).await.status(), 200);
    }
    assert_eq!(
        identity(&state, "INTERACTIVE_TOKEN")
            .budget
            .snapshot()
            .outstanding,
        0
    );
    let request = submit(&state, "AGENT_TOKEN_2", TaskAction::GetProduct(300));
    let (id, release) = executor.selected.recv().await.unwrap();
    assert_eq!(id, 300);
    release.send(()).unwrap();
    assert_eq!(finish(request).await.status(), 200);
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test(start_paused = true)]
async fn earlier_new_deadline_rearms_the_single_scheduler_timer() {
    let state = test_state();
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    budget.set_maximum(20);
    let mut executor = executor(state.clone());
    let mut requests = Vec::new();
    for index in 0..10 {
        requests.push(submit(
            &state,
            "AGENT_TOKEN_1",
            TaskAction::GetProduct(index),
        ));
    }
    let mut running = Vec::new();
    for _ in 0..10 {
        running.push(executor.selected.recv().await.unwrap().1);
    }
    let agent = submit(&state, "AGENT_TOKEN_1", TaskAction::GetProduct(100));
    slots(&budget, 9).await;
    advance(Duration::from_secs(1)).await;
    let interactive = submit(&state, "INTERACTIVE_TOKEN", TaskAction::GetProduct(200));
    slots(&identity(&state, "INTERACTIVE_TOKEN").budget, 9).await;
    advance(Duration::from_secs(2)).await;
    assert_queue_timeout(finish(interactive).await).await;
    assert!(!agent.is_finished());
    advance(Duration::from_secs(7)).await;
    assert_queue_timeout(finish(agent).await).await;
    assert!(executor.selected.try_recv().is_err());
    for release in running {
        release.send(()).unwrap();
    }
    for request in requests {
        assert_eq!(finish(request).await.status(), 200);
    }
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test(start_paused = true)]
async fn cancellation_expiry_and_dispatch_boundary_release_exactly_once() {
    let budget = Budget::new(1);
    let mut queues = Queues::default();
    // Exercise both serialized outcomes of cancellation racing with timeout.
    for cancel_first in [true, false] {
        let (job, receiver) = make_job(&budget, 1, Duration::from_secs(1));
        queues.agents.push_back(job);
        advance(Duration::from_secs(1)).await;
        if cancel_first {
            drop(receiver);
            queues.maintain(Instant::now());
        } else {
            queues.maintain(Instant::now());
            drop(receiver);
        }
        queues.maintain(Instant::now());
        assert_eq!(budget.snapshot().outstanding, 0);
        assert!(queues.pop_next().is_none());
    }
    let (job, receiver) = make_job(&budget, 2, Duration::from_secs(1));
    queues.agents.push_back(job);
    advance(Duration::from_secs(1)).await;
    assert!(queues.pop_next().is_none(), "Deadline equality is expired");
    assert_queue_timeout(receiver.await.unwrap()).await;
    let (job, mut receiver) = make_job(&budget, 3, Duration::from_secs(1));
    queues.agents.push_back(job);
    advance(Duration::from_millis(999)).await;
    let selected = queues.pop_next().unwrap();
    advance(Duration::from_secs(5)).await;
    queues.maintain(Instant::now());
    assert!(receiver.try_recv().is_err());
    assert_eq!(budget.snapshot().outstanding, 1);
    drop(selected.permit);
    selected.reply.send(StatusCode::OK.into_response()).unwrap();
    assert_eq!(receiver.await.unwrap().status(), 200);
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test]
async fn policy_publishes_class_specific_queue_wait_and_remains_uncacheable() {
    let state = AppState::with_queue_settings(
        identities_from(|name| Ok(format!("test-{name}"))).unwrap(),
        false,
        None,
        QueueSettings {
            interactive: Duration::from_millis(123),
            agent: Duration::from_millis(456),
        },
    );
    let server = serve(app(state)).await;
    for (name, expected) in [("INTERACTIVE_TOKEN", 123), ("AGENT_TOKEN_1", 456)] {
        let response = http()
            .get(format!("{}/agent-policy", server.url))
            .bearer_auth(format!("test-{name}"))
            .send()
            .await
            .unwrap();
        assert_eq!(response.headers()["cache-control"], "no-store");
        let body: serde_json::Value = response.json().await.unwrap();
        assert_eq!(body["version"], 3);
        assert_eq!(body["queue"]["max_wait_ms"], expected);
    }
}

#[tokio::test(start_paused = true)]
async fn fifo_merges_acceptance_order_across_classes_even_at_equal_times() {
    let budget = Budget::new(100);
    let mut queues = Queues::default();
    queues.mode = SchedulerMode::Fifo;
    let mut receivers = Vec::new();
    for (index, class) in [false, false, true, false, true, true]
        .into_iter()
        .enumerate()
    {
        let (mut job, rx) = make_job(&budget, index as u64, Duration::from_secs(10));
        job.order = queues.take_order();
        if class {
            queues.interactive.push_back(job);
        } else {
            queues.agents.push_back(job);
        }
        receivers.push(rx);
    }
    for expected in 0..6 {
        assert_eq!(id(&queues.pop_next().unwrap()), expected);
    }
    assert_eq!(budget.snapshot().outstanding, 0);
}
