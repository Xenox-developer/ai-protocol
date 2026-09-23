# Second service integration report

Date: 2026-09-23. This is a targeted compatibility/integration check, not a new
load comparison. Historical stage 4/4.1, multi-client and dispatcher results were
preserved. No commit, push, paid API, LLM call or user-server shutdown was made.

## Result

A separate support-ticket HTTP process now works through the same gateway and
local dispatcher as the catalog. A single discovery-driven Python client calls
both services without branches for ticket operation names. Integration is
**manual mapping**, not automatic onboarding of arbitrary APIs.

The final real-process run passed, with raw output in
[`results/second-service-final/`](results/second-service-final/result.json).
The earlier successful smoke run remains in `results/second-service/`.

| Observed check | Result |
|---|---|
| Discovery → schema-based operation selection → dispatcher → gateway → support upstream | Structured ticket JSON returned; matches direct-call result |
| Existing catalog via the same client | Product JSON preserved, both direct and dispatched |
| Support read-only credential discovery | Only `get_ticket` published |
| Forced search with that restricted credential | HTTP 403, one attempt, **zero upstream calls** |
| Allowed lookup with the same credential | Ticket 101 returned |
| Unknown operation / extra agent-supplied URL field | Local invalid request / gateway 422 before admission |
| Same owner ID and credential values across services | Independent gateway budgets and dispatcher gates |
| Limit changes | Catalog 5 → 1 → 3; support 5 → 2, remaining 2 after catalog recovery |
| Policy application | Catalog dispatcher observed 3; support dispatcher independently retained 2 |
| Concurrent calls | 12/12 successful, six per service |
| New admissions over the relevant limit | 0 |
| Final server outstanding | Catalog 0, support 0 |
| Final dispatcher active attempts | Catalog 0, support 0 |
| Service Authorization forwarded to support upstream | Never |

The final dispatcher traces contain seven catalog work attempts and nine support
work attempts (eight successes plus the intended 403), four discovery requests
per dispatcher, and no 429. These are small integration observations, not a
claim that 429 cannot occur. Direct discovery/checks are separate from these
dispatcher counters. Support's upstream trace contains ten calls: one readiness
probe, one direct search, one dispatched search, one allowed lookup and six
concurrent searches. The forbidden search contributed none. Gateway admissions
are eight catalog and nine support, including the direct work requests.

## Exact implementation changes

- `services/catalog.json`: move the existing operation and token-role definitions
  out of gateway handlers; it remains the embedded default. Existing launch
  commands, routes, role names and initial budgets are preserved.
- `src/service.rs` (225 lines including whitespace/comments): shared manifest
  parsing, validation, initial shared budgets, generated operation schemas and
  typed query mapping. No dependency was added. Owner-controlled origins and
  routes are validated; agents supply parameter values only.
- `src/main.rs`: register configured operation routes, authorize by operation
  name, publish filtered descriptors and additive `service_id`, validate input
  before admission, then pass a mapped request to the existing scheduler.
  Identity strings are owned to support configured names. Historical catalog
  fake actions remain test-only for the existing queue/barrier tests. The
  scheduler, budget implementation, class selection and timer logic are unchanged.
- `src/bin/client/transport.rs`: share typed discovery HTTP fetching. The
  dispatcher reads descriptors and builds a route table instead of accepting
  only two hardcoded catalog names. Legacy route aliases remain compatible.
  No token privilege is transferred: the original job token is always sent.
- `src/bin/dispatcher.rs`: explicitly bind the existing single queue/Gate/poller
  to one configured normalized `SERVICE_URL` origin and verified owner/class;
  check service ID consistency at startup and refresh. A changed ID pauses new
  attempts. Separate origins use separate dispatcher processes/private sockets.
- `examples/protocol_client.py`: optional explicit credential and service origin;
  generic discovery and execution retain validation and bounded retries.
- `examples/dispatch_client.py`: accept discovered descriptors (validating input)
  or operation names/legacy aliases, keeping the original deadline and no-replay
  behavior. Package imports and script imports both work.
- `examples/discover_client.py`: optional `DISPATCH_SOCKET` selects shared
  dispatch; selection/validation remain driven by discovery, not service names.
- Add targeted manifest/client/dispatcher tests, the real-process integration
  demo, owner/agent instructions, and update README, protocol and parent project
  context. Existing tests are retained.

## Service-specific onboarding cost

To add this second service, the owner supplies these three files:

| File | Size | Purpose |
|---|---:|---|
| `examples/support_service.py` | 46 lines | Independent HTTP API, including an optional route-only test trace |
| `examples/support_tickets.json` | 5 lines, three records | Local test data |
| `services/support.json` | 63 lines | Two operation mappings and three credential roles/budgets |

There is **zero support-specific Rust adapter code and zero support-specific
client code**. The 225-line common mapper was necessary because the gateway
previously embedded catalog routes, schemas and token grants. The 63-line
manifest is still an explicit service-specific declarative adapter: `query`
maps to upstream `text`, and `id` maps to `ticket_id`. Nothing infers these API
relationships automatically.

For an existing API with the supported read shape, only a manifest, credential
environment values, upstream origin, gateway address and optional dispatcher
socket/configuration are needed. To run this example, the owner additionally
launches its independent fixture service. The full command sequence and agent
connection examples are in [service-integration.md](../docs/service-integration.md).

## Verification and reproduction

Commands actually executed successfully from the repository root:

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo build --locked --bins
cargo test --locked --all-targets
python3 -m unittest discover -s tests -v
python3 -m compileall -q examples tests
python3 examples/service_integration_demo.py --output integration/results/second-service-final
git diff --check
```

Results: **52 Rust test executions passed** (28 gateway, 13 dispatcher, 8 load,
3 benchmark client), **29 Python tests passed**, formatting/check/build/bytecode
compilation passed. Existing tests cover authorization, permissions, weighted
3:1/FIFO scheduling, queue deadlines/cancellation, dynamic shrink accounting,
five-attempt retries and unknown execution after response loss. New semaphore-
controlled dispatcher checks prove a saturated gate for one origin cannot block
another origin with the same principal and credentials; service identity changes
pause sending. Manifest tests reject unsafe routes/origins and invalid grants or
inconsistent shared budgets. Python checks call an unrelated `lookup_case`
descriptor to avoid merely special-casing the new ticket operation names.

For another run, use a **new** output directory. The demo selects free local
ports, creates temporary tokens, starts only its own children and stops only those
children. Private socket directories are temporary. No credential values are
written to results. Discovery snapshots, per-gateway admissions/samples,
per-dispatcher attempts/discovery/final state, upstream route observations and
structured responses are retained. See also [verification metadata](verification.json).

## Limits of the result

- The mapper supports required strings and non-negative u64 integers in a flat
  object, gateway POST to upstream GET query parameters, and unchanged JSON
  responses. It is not a general schema engine, API importer or response adapter.
  Other upstream shapes need an adapter extension or owner-provided endpoint.
- One gateway process represents one service, and one dispatcher represents one
  service-origin/owner pair. Multiple origins/owners need separate instances.
  Equal service IDs are not global identities; aliases for one gateway are not
  automatically deduplicated. There is no multi-replica budget coordination.
- Policy describes an aggregate owner budget and does not reserve places.
  Independent callers or policy lag can cause 429. Only connected agents share
  the local dispatcher gate. It remains a single point of failure with no durable
  restart recovery or replay of unknown operations.
- Schema/route/credential changes require restart. Authentication is the existing
  environment-token model. Neither upstream credentials nor a new auth system,
  distributed locks, leases, UI or AIP was introduced.
- The generic direct Python path retains its prior retry behavior; the shared
  dispatcher provides adaptive owner-wide attempt limiting. Historical Rust load
  producers still define catalog workloads and are not the generic discovery CLI.
- Full load series were not repeated. This result establishes the demonstrated
  integration and isolation properties, not production security or performance.
