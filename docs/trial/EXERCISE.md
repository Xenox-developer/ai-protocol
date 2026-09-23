# Exercise: expose a new read operation without editing Rust

Goal: as a service owner, add one operation to an API mapping; then, as an agent
developer, discover and call it. Record elapsed time and confusing steps in the
[feedback form](FEEDBACK.md). Do not edit `src/` or add protocol capabilities.

## Start with the template

Copy `services/template.json` to a temporary working file. It is valid JSON,
not JSON-with-comments: unknown fields/comments are rejected. Explanations:

| Field | What the service owner chooses |
|---|---|
| `service_id` | A local descriptive identifier; not a global identity |
| `operations[].name` | Stable operation name used in discovery and grants |
| `description` | Plain-language purpose for the agent developer |
| `path` | Gateway POST path; use `/`-separated ASCII letters/digits/`_`/`-`; no URL, query, fragment, placeholders or reserved `/agent-policy` and `/admin` paths |
| `upstream_path` | Exact path appended to the owner's `UPSTREAM_URL` |
| `parameters` | All required gateway JSON fields; each has `type` (`string` or nonnegative `integer`/u64) and the upstream query-key name |
| `credentials[].token_env` | Name of an environment variable holding an existing Bearer credential; never its value |
| `required` | Whether an absent credential must fail startup |
| `principal_id`, `client_class` | Server-assigned owner and `agent`/`interactive` class |
| `credentials[].operations` | Allowed operation names for this specific token |
| `max_outstanding` | Shared initial owner/class budget; every token of the same owner/class must specify the same value |

The runnable template maps `read_record`, POST `/records/read`, body `{"id":101}`,
to GET `/api/tickets/get?ticket_id=101`. Its full and optional restricted credentials
initially have the same read permission. Credentials are distinct values even when
their budgets are shared. Restart the gateway/dispatcher after changing mappings.

## Local training API

Complete the [quickstart dependency installation](../QUICKSTART.md) first. For the
exercise, use Bash in one terminal with `.venv` active. The subshell below owns its
processes and cleans up on normal exit or interruption. It does not source `.env`.
Choose unused ports if you change these commands; never stop another server.

```sh
(
  trial_dir="$(mktemp -d /tmp/ai-exercise.XXXXXX)"
  upstream_pid= gateway_pid=
  trap 'if [ -n "$gateway_pid" ]; then kill "$gateway_pid" 2>/dev/null; wait "$gateway_pid" 2>/dev/null; fi; if [ -n "$upstream_pid" ]; then kill "$upstream_pid" 2>/dev/null; wait "$upstream_pid" 2>/dev/null; fi; rm -rf "$trial_dir"' EXIT
  trap 'exit 130' INT TERM
  cp services/template.json "$trial_dir/service.json"
  read -r SUPPORT_PORT GATEWAY_PORT < <(python - <<'PY'
import socket
with socket.socket() as a, socket.socket() as b:
    a.bind(('127.0.0.1', 0)); b.bind(('127.0.0.1', 0))
    print(a.getsockname()[1], b.getsockname()[1])
PY
  )
  export SUPPORT_PORT GATEWAY_PORT
  export SERVICE_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export SERVICE_READ_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export UPSTREAM_URL="http://127.0.0.1:$SUPPORT_PORT"
  export SERVICE_URL="http://127.0.0.1:$GATEWAY_PORT"
  export SERVICE_CONFIG="$trial_dir/service.json"
  export AGENT_TOKEN_1="$SERVICE_AGENT_TOKEN"
  unset DISPATCH_SOCKET ADMIN_TOKEN BENCHMARK_TRACE_PATH TEST_429
  python examples/support_service.py & upstream_pid=$!
  ./target/debug/ai-protocol & gateway_pid=$!
  echo "Edit $SERVICE_CONFIG; never paste token values into it."
  echo 'Wait for both startup messages, then press Enter.'
  read -r
  python examples/discover_client.py
  # Choose read_record, parameters {"id":101}; expect the Password reset ticket.
  echo 'Add your operation now. Press Enter to restart the owned gateway.'
  read -r
  kill "$gateway_pid"; wait "$gateway_pid" 2>/dev/null; gateway_pid=
  ./target/debug/ai-protocol & gateway_pid=$!
  echo 'Wait for the gateway startup message, then press Enter.'
  read -r
  python examples/discover_client.py
  echo 'Press Enter to stop the exercise processes and remove temporary files.'
  read -r
)
```

For editing, use another terminal/editor and the printed temporary manifest path.
Save your manifest without any credential values elsewhere if you want to retain
it before final cleanup. Temporary ports have a small bind race; on bind failure,
exit the subshell and retry, without killing unrelated processes.

The existing local upstream also supports:

```text
GET /api/tickets/search?text=password
→ {"tickets":[...]} (two matching tickets)
```

Your task:

1. Add a new operation named `find_records`, with a new gateway path of your
   choice, a useful description and one required string field named `phrase`.
2. Map that field to the upstream query key `text` and the exact search route.
3. Grant it to `SERVICE_AGENT_TOKEN` only. Leave `SERVICE_READ_TOKEN` restricted
   to `read_record`; both continue to share five slots for `practice-owner`.
4. Restart the owned gateway, discover the new operation and call it with
   `{"phrase":"password"}`. It should return two records without Rust edits.
5. In the exercise shell, temporarily select the restricted token with
   `AGENT_TOKEN_1="$SERVICE_READ_TOKEN" python examples/discover_client.py`.
   It should discover only `read_record`. A forced HTTP call to the new route
   with this token must return 403, not inherit the full token's grant.

The script pauses for another terminal to edit the manifest. To run the optional
restricted-token command in that same shell, insert it after the second client
command before pasting the block. The automated sample solution is exercised by
`python tests/trial_manifest.py`; it checks template startup, mapping, discovery,
403 and zero outstanding. Running it is not a substitute for trying the exercise.

## Using your own API instead

Run it separately and set its fixed origin in `UPSTREAM_URL`; set the manifest's
exact upstream route and query keys to match its documentation. Keep it local for
this first trial. An arbitrary URL cannot come from agent input. You do not need
an API-specific Rust branch **if** this existing transformation fits:

**POST JSON → GET query**, flat object, all fields required, strings or nonnegative
u64 integers, automatically URL-encoded query values, unchanged JSON response.

There is no support for optional/nested fields, arrays, enums, arbitrary JSON
Schema constraints, dynamic path segments, POST upstream bodies, pagination
orchestration, response reshaping, upstream authentication, writes or automatic
OpenAPI import. If your API needs these, record the missing transformation rather
than modifying the protocol for this trial. Budget state is per gateway process;
a separate service with the same owner ID does not share that counter.
