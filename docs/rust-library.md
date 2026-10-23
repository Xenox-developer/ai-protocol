# Embedding the Rust gateway

The `ai-protocol` package now provides a Rust library and the existing standalone
gateway. Both use the same authorization, shared budgets, queues, scheduler and
protocol v3 routes. Existing environment settings and launch commands remain
supported. No dependency was added for this extraction.

## Manifest-based integration

Use `GatewayConfig::from_manifest(manifest, upstream_origin, credential_resolver)`
to configure a gateway without reading process environment variables. The resolver
maps the manifest's token variable names to application-owned credential values.
Missing credentials use `std::env::VarError::NotPresent`, including optional roles.
Alternatively, `GatewayConfig::from_env()` loads the existing standalone settings.

Call `Gateway::start(config).await?` inside a Tokio runtime, then merge
`gateway.router()` into your Axum application. Keep the `Gateway` value alive
while serving HTTP. Repeated `router()` calls share the same state and scheduler;
they do not multiply owner budgets. Configuration errors are returned as
`ConfigError`, never by terminating the host application. Startup also returns
`ConfigError` when runtime/client initialization is unavailable.

Optional configuration methods set the separate admin token, queue waiting
deadlines and scheduler mode. Queue waits must be whole milliseconds from 1
through 3600000. Execution capacity defaults to ten, queue capacity to 32 per
class, and execution timeout to five seconds. Resource bounds are configurable.

The host application owns its HTTP listener and server lifecycle. The gateway
does not install signal handlers or choose a listen address for an embedded app.

The external-crate test in `tests/library.rs` demonstrates a custom manifest
and a gateway merged with an application route, using only public interfaces:

```sh
cargo test --locked --test library
cargo doc --locked --no-deps
python3 scripts/check_rust_library.py
```

## Native read-only handlers

`Operation::new(name, description, path, input_schema, handler)` registers an
async handler whose input type implements `Deserialize` and whose result
implements Axum's `IntoResponse`. `GatewayConfig::native(service_id, operations,
credentials)` builds a service without a manifest, environment access or upstream
HTTP server. `Credential` contains a bearer token, owner, class, operation grants
and initial budget. Supply values from the application's own configuration or
credential store. These credentials are a startup snapshot. For live authentication and revocation,
use `native_authenticated` as described below.

The complete [native document service](../examples/native_service.rs) uses only
public interfaces. It searches local, read-only document titles, without an LLM.
Set `NATIVE_AGENT_TOKEN` to a privately generated token, then run:

```sh
cargo run --locked --example native_service
```

It defaults to `127.0.0.1:3000`; `NATIVE_LISTEN_ADDR` selects another address,
including `127.0.0.1:0` for an OS-selected free port. The selected address is
printed without credentials. For example, a client can discover `/agent-policy` and
call `POST /documents/find` with `{"query":"rust"}` and its bearer credential.
It receives `{"documents":["Rust introduction"]}`.

Authorization runs before body validation. Serde deserialization runs before
admission, and failures return 422 without consuming budget. The handler is
called only after scheduler selection, so both queued and running handlers count
against the same owner budget. Handlers must be nonblocking and read-only; the
framework does not infer side effects or make arbitrary work safely retryable.
Native handlers and response materialization use the configured execution timeout
(five seconds by default). The application must also bound its own allocations.

The owner supplies the discovery schema and keeps it consistent with the input
type. Nested structures, arrays, booleans and optional fields work through Serde.
Use `#[serde(deny_unknown_fields)]` where the schema rejects additional properties.
Constraints such as string patterns or numeric ranges require corresponding
Serde/custom application validation; the library does not automatically enforce
arbitrary JSON Schema keywords. Avoid secret values in schemas or descriptions.

Native handlers and mapped upstream operations use the same queue, permits,
authorization, admin budget updates, deadlines and scheduler. The standalone
manifest mapper retains its existing, narrower API shape documented in
[service integration](service-integration.md); its manifests and wire responses
remain compatible.

## Budget changes and lifecycle

`gateway.set_agent_limit(owner_id, maximum)` applies the same owner-wide agent
budget update as the administrative HTTP endpoint. It returns a coherent
`BudgetSnapshot` with `maximum`, `outstanding` and `revision`. Bounds remain
1..=1000; unknown agent owners return an error. A reduction preserves all accepted
work, even when outstanding temporarily exceeds the new maximum. An identical
update does not change the revision. This method is for trusted host code; it
does not grant HTTP callers administrative access.

`gateway.close().await` stops new admissions while keeping discovery available.
`gateway.shutdown().await` closes admission, waits for accepted work to finish
or reach its existing queue deadline, then stops the background tasks. The
application must separately stop its HTTP listener. The native example handles
Ctrl+C through Axum's graceful server shutdown and then drains the gateway.

Valid new operation calls after closure return 503 and:

```json
{"error":{"code":"gateway_closed","execution":"not_started"}}
```

There is no promised restart delay and no automatic retry authorization for this
error. Existing clients retain their queue_timeout-only 503 retry rule. The
standalone executable closes admission and drains on SIGINT/SIGTERM with a
configurable whole-server grace deadline.

Dropping a gateway, or cancelling its consuming `shutdown()` future, performs
abrupt cleanup: new work is rejected, work still queued at cleanup is rejected
without execution, and locally running futures are aborted. Work concurrently
handed to an executor may already have started. A retained router remains closed.
Already started operations can have unknown outcomes, especially upstream HTTP
requests; local cancellation does not prove downstream execution stopped.
Use explicit shutdown for draining and, when needed, wrap it in an
application-selected `tokio::time::timeout`. Native handlers use the configured
execution timeout; the application must also bound the whole HTTP shutdown.

## Use from a separate application

Until a package release is published, add a path dependency to your checkout:

```toml
[dependencies]
ai-protocol = { path = "/path/to/ai-protocol" }
axum = "0.8.9"
serde = { version = "1.0.229", features = ["derive"] }
serde_json = "1.0.151"
tokio = { version = "1.53.1", features = ["full"] }
```

Use the project's pinned Rust toolchain for the current supported recipe. Copy
the native example into your application's `src/main.rs`, provide its own token,
and run it. Public interfaces remain experimental; no stable 1.0 API or broad
minimum-Rust-version matrix is claimed.

`scripts/check_rust_library.py` automates that external-consumer path in a temporary
directory, with a separate Cargo manifest and only public imports. It reuses
downloaded dependencies/build outputs, creates a temporary in-memory token,
binds a free loopback port, checks discovery and a native call, then sends Ctrl+C
only to its own child and verifies successful exit. It proves source integration,
not installation of a published crate. Both this check and Rust documentation
checks are included in the regular Unix verification script.

## Resource bounds and existing authentication

`GatewayConfig::with_runtime_settings(RuntimeSettings { ..Default::default() })`
sets execution slots, per-class waiting capacity, execution deadline and request/
response byte bounds. `with_upstream_bearer_token` configures a separate secret for
mapped HTTP operations. `with_request_logging(true)` enables safe JSON summaries.
See [operations](operations.md) for defaults, signals, probes and metrics.

`GatewayConfig::native_authenticated` accepts `Vec<AccessGrant>` and an async
`Fn(String) -> Future<Output = Option<String>>`. The callback receives the supplied
bearer token and returns a configured grant ID only after your session/token
store verifies it. A missing/unknown ID or one-second callback timeout returns
401. Callback failures should return `None`. Grant IDs are non-secret labels;
they cannot authenticate requests themselves. Each grant declares owner, trusted
class, allowed operations and initial shared budget. No unbounded caller-created
budget registry is created. Revocation affects new HTTP attempts; accepted work
retains its permit and is not retroactively cancelled. The admin token remains a
separate static credential and is never sent to this callback.

```rust,ignore
let sessions = std::sync::Arc::new(your_session_store);
let config = GatewayConfig::native_authenticated(
    "documents", operations, grants,
    move |bearer| {
        let sessions = sessions.clone();
        async move { sessions.verify_and_find_grant(bearer).await }
    },
)?;
```

This sketch adapts your existing store; `tests/library.rs` contains a runnable
revocation test. Static `Credential` constructors retain their existing behavior.
