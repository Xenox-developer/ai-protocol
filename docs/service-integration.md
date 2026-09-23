# Integrating a second service

The gateway now hosts one configured service per process. The included support
API is independent of the protocol: it serves ordinary read-only HTTP endpoints
and local JSON fixtures. Integration uses **manual API mapping**, not automatic
API discovery or an OpenAPI importer. No service-specific branch was added to
any agent client.

## What the service owner supplies

1. Run the upstream HTTP service. The example is `examples/support_service.py`,
   backed by `examples/support_tickets.json`. An existing compatible API can be
   used instead; it does not need to implement policy, budgets or agent auth.
2. Create a manifest such as `services/support.json`. `service_id` identifies the
   service; `operations` defines stable names, descriptions and public POST paths.
   Each operation supplies an exact `upstream_path` and a `parameters` map. For
   each parameter, `type` is `string` or `integer` and `upstream` is its query key.
3. In `credentials`, map **environment variable names**, never secret values, to
   `principal_id`, `client_class`, permitted operation names and initial
   `max_outstanding`. Set `required` for each credential. Tokens sharing an owner
   and class must specify the same initial budget; inconsistent settings fail
   startup. Missing required, malformed or duplicate credentials fail closed.
4. Start the Rust gateway with `SERVICE_CONFIG`, `UPSTREAM_URL` and a free
   `GATEWAY_PORT`. The upstream address is an owner-selected HTTP(S) origin,
   without credentials, path, query or fragment. It is never taken from a job.
   A custom service requires explicit `UPSTREAM_URL` (except the catalog default).
5. Optionally launch a dispatcher for this gateway origin and owner. Use a
   separate private socket for each service/owner pair. Register the existing
   agent tokens through `DISPATCH_TOKEN_VARS`.

The support mapping is:

| Discovered operation | Gateway POST | Upstream GET | Parameter mapping |
|---|---|---|---|
| `search_tickets` | `/tickets/search` | `/api/tickets/search` | `query` → `text` |
| `get_ticket` | `/tickets/get` | `/api/tickets/get` | `id` → `ticket_id` |

All declared parameters are required. Extra fields, invalid types and negative
integers are rejected before admission. Integers use Rust's `u64` range. The
manifest generates the published JSON Schema and the gateway validation from
the same parameter definitions. This small adapter supports POST JSON objects
mapped to GET query parameters, with unchanged upstream JSON response bodies.
It does not support optional/nested fields, arbitrary schema keywords, path
parameter substitution, response transformations, writes or upstream credentials.
Those APIs would require extending the adapter or providing a service-owned
translation endpoint. Invalid/reserved routes, inconsistent budgets, unknown
permissions and unsupported manifest fields fail startup.

Parameters are URL-encoded; their values cannot replace the configured origin
or route. Redirects are disabled. Service tokens are checked by the gateway and
are not forwarded to the upstream. The five-second upstream timeout remains
separate from accepted queue waiting time and the original client task deadline.

## Run support alongside the catalog

First install the existing Python requirements and build the binaries as in the
README. Choose available ports; these example values are not reservations. Do
not stop a process already using them. The automatic demo below selects free
ports and cleans up only its own children.

Generate temporary credentials in the owner's shell without printing them:

```sh
export SUPPORT_AGENT_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export SUPPORT_READ_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export SUPPORT_INTERACTIVE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export SUPPORT_PORT=4100
export GATEWAY_PORT=3100
export SERVICE_CONFIG="$PWD/services/support.json"
export UPSTREAM_URL="http://127.0.0.1:$SUPPORT_PORT"
```

Start the upstream and gateway in separate terminals with the relevant variables
available in each (the upstream only needs `SUPPORT_PORT`):

```sh
python3 examples/support_service.py
# In the gateway terminal, with the owner configuration above:
./target/debug/ai-protocol
```

The support manifest gives `SUPPORT_AGENT_TOKEN` both operations and
`SUPPORT_READ_TOKEN` only `get_ticket`. Both share five agent slots for
`demo-owner`; `SUPPORT_INTERACTIVE_TOKEN` has a separate budget of ten. The
catalog's owner happens to have the same ID, but its counter is independent.
Optional `ADMIN_TOKEN` enables the existing administrative API **on this service
origin**. Queue waits still use `AGENT_MAX_WAIT_MS` and `INTERACTIVE_MAX_WAIT_MS`.
Manifest changes require a gateway restart; operation/token registration is not
an administrative API. Restart the dispatcher after changing routes or identity.

For a dispatcher, in the owner's configured shell:

```sh
export SERVICE_URL="http://127.0.0.1:$GATEWAY_PORT"
export DISPATCH_SOCKET_DIR="$(mktemp -d /tmp/support-dispatch.XXXXXX)"
chmod 700 "$DISPATCH_SOCKET_DIR"
export DISPATCH_SOCKET="$DISPATCH_SOCKET_DIR/socket"
export DISPATCH_TOKEN_VARS=SUPPORT_AGENT_TOKEN,SUPPORT_READ_TOKEN
./target/debug/dispatcher
```

The dispatcher verifies each registered token's owner and agent class, builds its
route table from permitted discovery descriptors, and refreshes policy through
one loop. All work still uses the submitting token, even if another token was
used to discover that route. Gateway authorization is authoritative. A known
forbidden route returns 403. A name absent from every startup descriptor returns
local `invalid_dispatch_request`, without HTTP. Route aliases (`search`,
`product` for the catalog) remain supported for existing local callers; new
callers should send discovered operation names. New routes require restart.

## Connect an agent without service-specific code

Give the agent only its own credential, the gateway origin and optionally the
matching private socket. Do not give it the owner's complete credential set.
`SERVICE_URL` selects the origin; `GATEWAY_PORT` remains a compatible localhost
fallback. With the agent's token in `AGENT_TOKEN_1`:

```sh
export SERVICE_URL=http://127.0.0.1:3100
# Set AGENT_TOKEN_1 securely to the agent's own support credential.
python3 examples/discover_client.py
```

The same CLI discovers descriptions and parameter schemas, asks which operation
to call, validates the JSON and invokes it. Set `DISPATCH_SOCKET` to use the local
dispatcher; leave it unset for direct calls. No operation names are coded into
this client. The selected socket must belong to the selected gateway/owner.
Discovery uses this agent's own token in both modes and does not reserve places.

The reusable functions also accept explicit per-call origins and credentials:

```python
from protocol_client import discover, execute, service_client
from dispatch_client import DispatcherClient

# The examples directory must be on Python's import path.
with service_client(own_token) as http:
    operations = discover(http, base_url=service_origin)
    operation = choose_operation(operations)  # Your application or user's choice.
    result = execute(http, operation, params, base_url=service_origin)

# Alternatively, use the same discovered descriptor and original deadline:
result = await DispatcherClient(socket_path, own_token).call(
    operation, params, deadline_unix_ms=original_task_deadline_ms
)
```

Direct Python calls retain their existing bounded retries but do not add a
shared local budget. The dispatcher adds the common queue, shared attempt gate
and adaptive refresh. `load` and historical benchmark producers retain their
catalog-specific workload definitions; they are not the general discovery CLI.

## Scope, compatibility and limits

`service_id` is an additive v3 discovery field. Existing clients may ignore it.
`limits.scope = "principal"` and `limits.max_outstanding` keep their existing
names and meaning: an aggregate owner/class limit in **one service gateway
process**, never a per-agent allocation. Policy reports a limit, not a reservation.
The dispatcher binds one Gate and one queue to one configured normalized origin
and verified owner/class; the advertised service ID must remain consistent.
Equal owner IDs, token values or even advertised IDs on different origins do not
merge gates. The ID alone is not a globally unique identity. Aliases for the same
physical gateway are not automatically deduplicated. Start one dispatcher per
actual service/owner, and direct all participating agents to it.

Multiple gateway replicas do not share counters. Multiple dispatchers for the
same gateway/owner do not coordinate. A dispatcher only coordinates connected
clients; independent callers may still cause 429. It is a single point of
failure, has a bounded queue and does not guarantee recovery after restart.
Unknown execution after response loss is not replayed. The five-attempt total,
Retry-After, allowed retry reasons and original deadline remain unchanged.

## Reproduce the targeted check

```sh
cargo build --locked --bins
python3 examples/service_integration_demo.py --output integration/results/my-run
```

Use a new output directory. The check launches two upstream processes, two real
gateways and two dispatchers with temporary credentials and free ports. It saves
policy snapshots, request/budget traces and `result.json`, with no tokens. It
checks generic discovery/calls, a restricted token, rejection of unpublished
operations/extra URL fields, separate budgets, adaptive refresh, concurrent
progress and final zero outstanding/active counts. The support server's optional
`SUPPORT_TRACE_PATH` records route names and whether an Authorization header was
present; no credentials, queries or ticket bodies are logged. The deterministic
Rust gate test additionally holds attempts with semaphores to prove that a full
service gate does not block the other service.

See [measured integration report](../integration/SECOND_SERVICE_REPORT.md).
