# First external trial (Unix, no LLM)

Run from the root of the provided `ai-protocol` source checkout/archive. Start a
timer now if you will send [feedback](trial/FEEDBACK.md). Do not use a historical
commit that lacks the trial files; the preparation report identifies the tested
working snapshot. No API account, service key or existing `.env` is needed.

## 1. Tools and dependencies

Use **Rust/Cargo 1.98.1** (pinned by `rust-toolchain.toml`), **Python 3.12**, Bash
and Git on macOS or Linux. This is the trial recipe, not a proven minimum-version
matrix. Native Windows is not supported by the Unix-socket dispatcher. Install
Python/Rust with your usual OS tooling; Rust installation needs `rustup`.
Native dependencies need a C/C++ compiler and build tools. On macOS install Xcode
Command Line Tools (`xcode-select --install`) and CMake; on Ubuntu install
`build-essential cmake git python3-venv` in addition to Python 3.12 and rustup.

```sh
rustup toolchain install 1.98.1 --profile minimal --component rustfmt
python3 --version                       # Use a Python 3.12 interpreter below.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cargo build --locked --bins
```

The first installation/build downloads public dependencies. Runtime service calls
are local. The OpenAI SDK is installed for existing mocked client tests; this
quickstart never invokes it or asks for an API key.

## 2. First call and multiple clients

```sh
python examples/quickstart.py
```

The script prints numbered steps. It starts the catalog and freshly built gateway
on free loopback ports, creates temporary tokens in memory, fetches
`GET /agent-policy`, and uses a discovered operation to send
`POST /product` with `{"id":2}`. The first response should contain:

```json
{"product":{"id":2,"name":"Brown boots","price":180}}
```

It then starts one dispatcher and **three separate Python client processes**, each
with its own token. All three call `get_product` through the same private Unix
socket and must return `ok: true`. Expected final lines include:

```text
PASS: first call, three dispatched clients, final outstanding=0.
5. Owned processes stopped; temporary credentials and socket removed.
```

The default command stops its children automatically. It does not load a local
`.env`, use ports 3000/4000, or stop unrelated servers. If a port is taken in the
small interval between selection and binding, startup fails visibly; rerun the
command instead of killing another server. Build errors, missing Python modules
or a failed child cause a nonzero result; report the relevant step and error.

## 3. Try an agent manually (optional)

```sh
python examples/quickstart.py --keep-running
```

After its checks, the script prints `source /tmp/.../agent.env`. In a second
terminal, go to the same checkout, activate `.venv`, then run that **printed**
source command. It gives this terminal one agent token, a gateway origin and the
matching socket; do not share the private file or its contents.

```sh
python examples/discover_client.py
```

Choose `get_product` and enter `{"id":2}`. This uses the dispatcher because
`DISPATCH_SOCKET` is set. To try the same discovery client directly:

```sh
(env -u DISPATCH_SOCKET python examples/discover_client.py)
```

To start three clients yourself (Bash):

```sh
python examples/dispatch_client.py get_product '{"id":2}' & client_a=$!
python examples/dispatch_client.py get_product '{"id":2}' & client_b=$!
python examples/dispatch_client.py get_product '{"id":2}' & client_c=$!
wait "$client_a" "$client_b" "$client_c"
```

These manual processes intentionally share this one agent credential; the default
script also checks three distinct credentials. To stop, press **Ctrl+C in the
first terminal**. It stops only its catalog/gateway/dispatcher children and
removes the temporary directory. Close the agent terminal to discard its copied
environment. Avoid `pkill`, `killall`, or reusing a stale socket path.

## Who does what next?

| Role | Responsibility |
|---|---|
| Service owner | Runs/protects the upstream; writes operation names/schemas through parameter definitions, exact API mappings, token permissions and initial owner/class budgets; runs the gateway and optionally updates budgets through its admin API |
| Agent developer | Receives only their credential and gateway origin; discovers and selects an operation, supplies parameters and the original deadline; connects directly or to the provided socket |
| Owner of several agents | Optionally runs one local dispatcher per actual service-origin/owner pair, registers the participating tokens and shares the private socket with those agents |

A single developer may fill all three roles in this local trial. Token grants are
still separate from discovery descriptions. A published budget is shared owner
capacity, not a personal allocation or reservation for each process.

Next: [manifest exercise](trial/EXERCISE.md), [detailed owner/agent setup](service-integration.md),
and [feedback form](trial/FEEDBACK.md). Our successful automated run does not
establish how clear these instructions will be to someone new to the project.
