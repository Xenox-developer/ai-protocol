use super::*;

fn admin_state() -> Arc<AppState> {
    AppState::new(
        identities_from(|name| Ok(format!("test-{name}"))).unwrap(),
        false,
        Some("test-admin".into()),
    )
}

async fn update(server: &Server, maximum: usize) -> serde_json::Value {
    let response = http()
        .patch(format!("{}/admin/principals/demo-owner/limits", server.url))
        .bearer_auth("test-admin")
        .json(&serde_json::json!({"max_outstanding": maximum}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["cache-control"], "no-store");
    response.json().await.unwrap()
}

async fn policy(server: &Server, name: &str) -> serde_json::Value {
    http()
        .get(format!("{}/agent-policy", server.url))
        .bearer_auth(format!("test-{name}"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap()
}

async fn complete_one(state: &AppState) {
    let job = state.queues.lock().await.agents.pop_front().unwrap();
    drop(job.permit);
    job.reply.send(StatusCode::OK.into_response()).unwrap();
}

#[tokio::test]
async fn shrink_and_grow_preserve_jobs_shared_budget_and_other_scopes() {
    let state = admin_state();
    let server = serve(app(state.clone())).await;
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let other_before = policy(&server, "OTHER_AGENT_TOKEN").await;
    let interactive_before = policy(&server, "INTERACTIVE_TOKEN").await;
    let mut tasks = Vec::new();
    for index in 0..5 {
        tasks.push(submit(
            &state,
            if index % 2 == 0 {
                "AGENT_TOKEN_1"
            } else {
                "AGENT_TOKEN_2"
            },
            CatalogAction::GetProduct(2),
        ));
    }
    slots(&budget, 0).await;
    let response = update(&server, 2).await;
    assert_eq!(response["policy_revision"], 2);
    assert_eq!(response["limits"]["max_outstanding"], 2);
    assert_eq!(response["outstanding"], 5);
    assert!(tasks.iter().all(|task| !task.is_finished()));
    assert_eq!(state.queues.lock().await.agents.len(), 5);
    for expected in (2..=5).rev() {
        assert_eq!(budget.snapshot().outstanding, expected);
        for name in ["AGENT_TOKEN_1", "AGENT_TOKEN_2"] {
            let response = finish(submit(&state, name, CatalogAction::Search("".into()))).await;
            assert_eq!(response.status(), 429);
        }
        complete_one(&state).await;
    }
    assert_eq!(budget.snapshot().outstanding, 1);
    tasks.push(submit(
        &state,
        "AGENT_TOKEN_2",
        CatalogAction::Search("".into()),
    ));
    slots(&budget, 0).await;
    let same = update(&server, 2).await;
    assert_eq!(same["policy_revision"], 2);
    let response = update(&server, 5).await;
    assert_eq!(response["policy_revision"], 3);
    assert_eq!(response["outstanding"], 2);
    for _ in 0..3 {
        tasks.push(submit(
            &state,
            "AGENT_TOKEN_1",
            CatalogAction::GetProduct(2),
        ));
    }
    slots(&budget, 0).await;
    assert_eq!(budget.snapshot().outstanding, 5);
    for name in ["AGENT_TOKEN_1", "AGENT_TOKEN_2"] {
        let current = policy(&server, name).await;
        assert_eq!(current["version"], 3);
        assert_eq!(current["policy_revision"], 3);
        assert_eq!(current["refresh_after_ms"], 1000);
        assert_eq!(current["limits"]["max_outstanding"], 5);
        assert_eq!(current["outstanding"], 5);
    }
    assert_eq!(policy(&server, "OTHER_AGENT_TOKEN").await, other_before);
    assert_eq!(
        policy(&server, "INTERACTIVE_TOKEN").await,
        interactive_before
    );
    for _ in 0..5 {
        complete_one(&state).await;
    }
    for task in tasks {
        assert_eq!(finish(task).await.status(), 200);
    }
    assert_eq!(budget.snapshot().outstanding, 0);
}

#[tokio::test]
async fn administrative_authentication_and_validation_fail_closed() {
    let state = admin_state();
    let server = serve(app(state.clone())).await;
    let url = format!("{}/admin/principals/demo-owner/limits", server.url);
    for (token, status) in [
        (None, 401),
        (Some("unknown"), 401),
        (Some("test-AGENT_TOKEN_1"), 403),
        (Some("test-INTERACTIVE_TOKEN"), 403),
    ] {
        let mut request = http()
            .patch(&url)
            .json(&serde_json::json!({"max_outstanding":2}));
        if let Some(token) = token {
            request = request.bearer_auth(token);
        }
        let response = request.send().await.unwrap();
        assert_eq!(response.status(), status);
        assert_eq!(response.headers()["cache-control"], "no-store");
    }
    let response = http()
        .patch(&url)
        .header("Authorization", "Bearer test-admin")
        .header("Authorization", "Bearer test-AGENT_TOKEN_1")
        .json(&serde_json::json!({"max_outstanding":2}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
    for body in [
        serde_json::json!({"max_outstanding":0}),
        serde_json::json!({"max_outstanding":1001}),
        serde_json::json!({"max_outstanding":-1}),
        serde_json::json!({"max_outstanding":2.5}),
        serde_json::json!({"max_outstanding":true}),
        serde_json::json!({"max_outstanding":"2"}),
        serde_json::json!({}),
        serde_json::json!({"max_outstanding":2,"client_class":"interactive"}),
    ] {
        let response = http()
            .patch(&url)
            .bearer_auth("test-admin")
            .json(&body)
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 422, "body: {body}");
    }
    let response = http()
        .patch(&url)
        .bearer_auth("test-admin")
        .header("Content-Type", "application/json")
        .body("{")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 400);
    let missing = http()
        .patch(format!("{}/admin/principals/missing/limits", server.url))
        .bearer_auth("test-admin")
        .json(&serde_json::json!({"max_outstanding":2}))
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status(), 404);
    assert_eq!(
        identity(&state, "AGENT_TOKEN_1").budget.snapshot().revision,
        1
    );
    assert_eq!(update(&server, 1).await["limits"]["max_outstanding"], 1);
    assert_eq!(
        update(&server, 1000).await["limits"]["max_outstanding"],
        1000
    );
    let response = http()
        .get(format!("{}/agent-policy", server.url))
        .bearer_auth("test-admin")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
    let disabled = serve(app(test_state())).await;
    let response = http()
        .patch(format!(
            "{}/admin/principals/demo-owner/limits",
            disabled.url
        ))
        .bearer_auth("test-admin")
        .json(&serde_json::json!({"max_outstanding":2}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 404);
    assert!(
        admin_token_from(Err(std::env::VarError::NotPresent), &state.identities)
            .unwrap()
            .is_none()
    );
    for token in [
        "",
        "invalid token",
        "test-AGENT_TOKEN_1",
        "test-INTERACTIVE_TOKEN",
    ] {
        assert!(admin_token_from(Ok(token.into()), &state.identities).is_err());
    }
}

#[test]
fn resizing_races_with_admission_completion_and_consistent_snapshots() {
    let budget = Budget::new(5);
    let barrier = std::sync::Barrier::new(4);
    std::thread::scope(|scope| {
        scope.spawn(|| {
            for index in 0..2000 {
                barrier.wait();
                budget.set_maximum(if index % 2 == 0 { 2 } else { 5 });
                barrier.wait();
            }
        });
        for _ in 0..2 {
            let budget = budget.clone();
            let barrier = &barrier;
            scope.spawn(move || {
                let mut held = Vec::new();
                for _ in 0..2000 {
                    barrier.wait();
                    if let Some(permit) = budget.try_acquire() {
                        held.push(permit);
                    }
                    if held.len() > 1 {
                        held.remove(0);
                    }
                    barrier.wait();
                }
            });
        }
        for _ in 0..2000 {
            barrier.wait();
            let snapshot = budget.snapshot();
            assert_eq!(
                snapshot.maximum,
                if snapshot.revision % 2 == 0 { 2 } else { 5 }
            );
            assert!(snapshot.outstanding <= 5);
            barrier.wait();
        }
    });
    assert_eq!(
        budget.snapshot(),
        budget::Snapshot {
            maximum: 5,
            revision: 2001,
            outstanding: 0
        }
    );
}

#[tokio::test]
async fn http_policy_revision_and_limit_remain_consistent_during_updates() {
    let state = admin_state();
    let server = serve(app(state.clone())).await;
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let writer = tokio::spawn(async move {
        for index in 0..1000 {
            budget.set_maximum(if index % 2 == 0 { 2 } else { 5 });
            tokio::task::yield_now().await;
        }
    });
    for _ in 0..40 {
        let policy = policy(&server, "AGENT_TOKEN_1").await;
        let revision = policy["policy_revision"].as_u64().unwrap();
        assert_eq!(
            policy["limits"]["max_outstanding"],
            if revision % 2 == 0 { 2 } else { 5 }
        );
    }
    writer.await.unwrap();
}

#[tokio::test]
async fn running_cancellation_after_shrink_keeps_the_original_accounting() {
    let state = admin_state();
    let server = serve(app(state.clone())).await;
    let mut upstream = upstream(StatusCode::OK).await;
    let scheduler = tokio::spawn(scheduler(state.clone(), upstream.server.url.clone()));
    let budget = identity(&state, "AGENT_TOKEN_1").budget;
    let mut tasks = Vec::new();
    for _ in 0..5 {
        tasks.push(submit(
            &state,
            "AGENT_TOKEN_1",
            CatalogAction::Search("".into()),
        ));
    }
    for _ in 0..5 {
        timeout(Duration::from_secs(3), upstream.started.recv())
            .await
            .unwrap()
            .unwrap();
    }
    assert_eq!(update(&server, 2).await["outstanding"], 5);
    let cancelled = tasks.pop().unwrap();
    cancelled.abort();
    assert!(cancelled.await.unwrap_err().is_cancelled());
    assert_eq!(budget.snapshot().outstanding, 5);
    assert_eq!(
        finish(submit(
            &state,
            "AGENT_TOKEN_2",
            CatalogAction::GetProduct(2)
        ))
        .await
        .status(),
        429
    );
    upstream.release.add_permits(4);
    slots(&budget, 1).await;
    tasks.push(submit(
        &state,
        "AGENT_TOKEN_2",
        CatalogAction::GetProduct(2),
    ));
    timeout(Duration::from_secs(3), upstream.started.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(budget.snapshot().outstanding, 2);
    upstream.release.add_permits(2);
    for task in tasks {
        assert_eq!(finish(task).await.status(), 200);
    }
    slots(&budget, 2).await;
    assert_eq!(budget.snapshot().outstanding, 0);
    scheduler.abort();
}
