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

Call `Gateway::start(config).await` inside a Tokio runtime, then merge
`gateway.router()` into your Axum application. Keep the `Gateway` value alive
while serving HTTP. Repeated `router()` calls share the same state and scheduler;
they do not multiply owner budgets. Configuration errors are returned as
`ConfigError`, never by terminating the host application.

Optional configuration methods set the separate admin token, queue waiting
deadlines and scheduler mode. Queue waits must be whole milliseconds from 1
through 3600000. Execution capacity remains ten, queue capacity remains 32 per
class, and the upstream timeout remains five seconds.

Dropping the gateway aborts its scheduler and diagnostics task. This is abrupt
local cleanup, not a guarantee that already sent upstream requests stopped.
The host application owns its HTTP listener and server lifecycle.

The external-crate test in `tests/library.rs` demonstrates a custom manifest
and a gateway merged with an application route, using only public interfaces:

```sh
cargo test --locked --test library
cargo doc --locked --no-deps
```

## Native read-only handlers

`Operation::new(name, description, path, input_schema, handler)` registers an
async handler whose input type implements `Deserialize` and whose result
implements Axum's `IntoResponse`. `GatewayConfig::native(service_id, operations,
credentials)` builds a service without a manifest, environment access or upstream
HTTP server. `Credential` contains a bearer token, owner, class, operation grants
and initial budget. Supply values from the application's own configuration or
credential store. Credentials remain a startup snapshot; runtime token rotation
and external per-request authentication adapters are not provided yet.

The complete [native document service](../examples/native_service.rs) uses only
public interfaces. It searches local, read-only document titles, without an LLM.
Set `NATIVE_AGENT_TOKEN` to a privately generated token, then run:

```sh
cargo run --locked --example native_service
```

It listens on `127.0.0.1:3000`. Use a free port; do not stop another service to
make room. For example, an application client can discover `/agent-policy` and
call `POST /documents/find` with `{"query":"rust"}` and its bearer credential.
It receives `{"documents":["Rust introduction"]}`.

Authorization runs before body validation. Serde deserialization runs before
admission, and failures return 422 without consuming budget. The handler is
called only after scheduler selection, so both queued and running handlers count
against the same owner budget. Handlers must be nonblocking and read-only; the
framework does not infer side effects or make arbitrary work safely retryable.
Native handlers have no implicit execution timeout. The application must bound
their execution, for example with `tokio::time::timeout`.

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
remain compatible. Draining shutdown is the next part of the extraction stage.
