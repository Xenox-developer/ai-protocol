# Versions and compatibility

| Identifier | Current value | Meaning |
| --- | --- | --- |
| Rust package `ai-protocol` | `0.1.0` | Public Rust interface; experimental pre-1.0 SemVer |
| Python package `ai-protocol-client` | `0.1.0` | Installed import `ai_protocol`; Python >=3.12 |
| Wire `version` | `3` | Discovery, authorization, admission and retry semantics |
| Manifest `format_version` | `1` | Owner-managed read-only HTTP mapping |
| `policy_revision` | starts at `1` | Per-process owner/class budget revision, not a protocol version |

The [0.1.0 release page](https://github.com/Xenox-developer/ai-protocol/releases/tag/v0.1.0)
records verified archives and current registry availability.
The tested Rust minimum and pinned toolchain are 1.98.1. Linux and macOS are the
supported dispatcher platforms. The SDK requires protocol v3 and rejects other
versions. Old v1/v2 clients must migrate; no v2 compatibility shim is promised.

Manifest files without `format_version` retain version 1 behavior. An explicit
unsupported version fails startup. JSON Schema files are shipped under
[`python/ai_protocol/schemas`](../python/ai_protocol/schemas): policy, operation,
manifest and error. Unknown discovery fields may be added; clients must ignore
them. Manifest unknown fields are errors. Structural schema checks complement
runtime checks for unique operation paths/names, grants and consistent budgets.
The server enforces native input through Serde and application validation; schema
validation in the SDK is a convenience, never the authorization boundary.

The framework extraction preserves the default catalog routes, manifests,
credentials, budgets, 3:1 selection, queue waiting deadlines and existing launch
commands. `examples/protocol_client.py` and `examples/dispatch_client.py` remain
compatibility entry points, backed by the installed package. Install the updated
requirements before running old example commands. New code should import
`ai_protocol` and use `AsyncClient` for policy refresh and local admission.

Gateway-generated errors now have a stable JSON shape rather than plain text.
Status codes, Retry-After and the v3 safe-retry rule are preserved. Code parsing
clients should use `error.code` and `error.execution`, not display text. Only 429
and 503 `queue_timeout`/`not_started` allow automatic retry, with one shared
five-attempt budget and original deadline. `gateway_closed` is terminal. Execution
failures are `unknown`, including timeouts and lost responses. Native non-success
responses become 502 `operation_failed`; handler error text is not exposed and a
handler's 429 cannot authorize replay after execution started. Domain read results
such as a missing record should use a successful JSON result (for example null).

The new default execution deadline covers native handlers and materialization of
both native/mapped responses. Defaults remain 10 executors, 32 waiting jobs per
class and five seconds of execution; body bounds are 2 MiB incoming and 16 MiB
outgoing. Oversize responses fail with 502 and unknown execution. Configure bounds
explicitly when adopting a workload that exceeds them.

Shrinking a budget preserves accepted work, so outstanding may temporarily exceed
the new maximum. A revision resets after process restart: recreate clients and
dispatchers after a server restart; they fail closed on revision regression or
identity change. Budgets are not shared between gateway processes. Static token
rotation requires restart; embedded applications can use live host authentication.

Before 1.0, breaking public package changes require a minor version increment and
a migration note. Fixes and additive compatible behavior use a patch release.
Incompatible wire semantics require a new protocol version; incompatible manifest
semantics require a new format version. Package numbers need not equal wire
versions. There is no LTS or independent security audit commitment in this release.

## Third-party conformance

After installing the SDK, validate a manifest without running a service:

```sh
python -m ai_protocol.conformance --manifest services/support.json
```

Check an explicitly selected service using an existing environment credential:

```sh
python -m ai_protocol.conformance --origin http://127.0.0.1:3000 --token-env AGENT_TOKEN_1
```

Optionally add `--operation get_product --params '{"id":2}'` to execute one known
read-only call. The checker verifies unauthenticated discovery failure, complete
policy/schema shape, safe operation descriptors, no-store, and the requested JSON
response. It does not generate overload against third-party services. The gateway's
Rust tests cover budget races, queue overflow/expiry, selection and cancellation;
the repository's smoke and service integration checks exercise real HTTP clients.
Passing the reusable checker alone does not prove scheduling conformance, safety
under arbitrary load, production security or independent developer usability.
