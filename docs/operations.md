# Operating a gateway

AI Protocol 0.1 supports a single in-memory gateway per service. Use a reverse
proxy for public HTTPS. Keep the upstream on a trusted private network, restrict
admin access, and use a separate upstream credential with read-only permissions.
This release has no distributed budgets or crash recovery.

## Configuration

The standalone executable reads these settings at startup. Invalid values fail
startup. `GatewayConfig` provides equivalent library builders; the host owns its
listener, shutdown deadline, authentication source and secret storage.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_LISTEN_ADDR` | `127.0.0.1:$GATEWAY_PORT` | Socket address; overrides port, allows port 0 |
| `GATEWAY_PORT` | `3000` | Legacy loopback port, 1–65535 |
| `SERVICE_CONFIG` | built-in catalog | Owner-supplied manifest file |
| `UPSTREAM_URL` | `http://127.0.0.1:$CATALOG_PORT` | Fixed origin; required for custom services |
| `CATALOG_PORT` | `4000` | Legacy catalog upstream port |
| `UPSTREAM_BEARER_TOKEN` | absent | Separate credential sent only to the configured upstream |
| `ADMIN_TOKEN` | absent | Distinct bearer credential enabling admin routes |
| `GATEWAY_EXECUTORS` | `10` | Execution slots, 1–1000 |
| `GATEWAY_QUEUE_CAPACITY` | `32` | Waiting jobs per class, 1–10000 |
| `INTERACTIVE_MAX_WAIT_MS` | `2000` | Accepted interactive queue deadline, 1–3600000 ms |
| `AGENT_MAX_WAIT_MS` | `10000` | Accepted agent queue deadline, 1–3600000 ms |
| `GATEWAY_EXECUTION_TIMEOUT_MS` | `5000` | Execution plus response-body deadline, 1–3600000 ms |
| `GATEWAY_MAX_REQUEST_BYTES` | `2097152` | JSON request bound, 1–67108864 bytes |
| `GATEWAY_MAX_RESPONSE_BYTES` | `16777216` | Buffered response bound, 1–67108864 bytes |
| `GATEWAY_SHUTDOWN_GRACE_MS` | `30000` | Whole HTTP/scheduler drain deadline, 1–3600000 ms |
| `GATEWAY_LOG_REQUESTS` | `false` | JSON completion summaries on stderr (`true`/`false`) |

See the manifest for required service credential variable names. `SCHEDULER_MODE`
is `weighted` by default; `fifo` is an explicit experiment mode. `TEST_429` and
`BENCHMARK_TRACE_PATH` are test/benchmark options, not deployment requirements.
Never place secret values in manifests, repository files, URLs or issue reports.

Queue waiting and execution have separate deadlines. Both consume the original
client deadline. Buffers are bounded per operation, not by one global byte pool:
size memory for execution slots × response bound plus queued request inputs.
Native handlers must avoid blocking the runtime, validate schema-specific rules,
and bound their own allocations. Response streams are materialized while holding
the execution permit, up to the configured limit. Streaming to the caller is not
supported. A timeout cancels local polling; it cannot prove remote work stopped.

## Health, readiness, metrics and logs

- `GET /healthz`: public process liveness (`200`).
- `GET /readyz`: public admission readiness (`200`, or `503` once closed).
- `GET /admin/metrics`: admin-authenticated JSON snapshot with cumulative
  `requests_total`, `failures_total`, current `active`, `queued_agent`,
  `queued_interactive`, and `closing`. Counters reset on restart; snapshots are
  observational, not transactions. Requests include probes/admin calls; failure
  means any non-2xx response. Scrape from the private admin network.
- Optional JSON request logs contain event, status and elapsed milliseconds.
  Tokens, paths, query strings, bodies and upstream error text are omitted.
  Lifecycle failures use safe messages. Host application logs remain its responsibility.

SIGINT and SIGTERM close admission, stop accepting HTTP connections, and drain
accepted work until the grace deadline. Expired waiters do not execute. Grace
expiry aborts local tasks and exits unsuccessfully; started remote outcomes are
unknown. Library owners call `close()` followed by `shutdown()` and bound the
whole server/drain future with their own deadline.

## HTTPS reverse proxy example

Example Caddy configuration (replace `api.example.com` with your domain):

```caddyfile
api.example.com {
    @admin path /admin /admin/*
    respond @admin 404
    reverse_proxy 127.0.0.1:3000
}
```

Bind the gateway to loopback and access admin routes locally with the separate
admin token. Configure DNS/TLS and proxy request/body/idle deadlines for your
deployment. Disable automatic proxy retries for operation POSTs, preserve
Authorization and Retry-After, and avoid logging authorization or request bodies.
Use a process supervisor and resource limits. Health endpoints do not verify
upstream availability or establish a production availability guarantee.
