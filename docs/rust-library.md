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

The manifest mapper remains limited to the read-only API shape documented in
[service integration](service-integration.md). Native handler registration and
draining shutdown are the next parts of the library extraction stage.
