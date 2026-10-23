# Contributing

Bug reports, documentation corrections and focused pull requests are welcome.
For substantial interface or protocol changes, open an issue describing the
problem and proposed behavior before investing in an implementation. Follow the
[code of conduct](CODE_OF_CONDUCT.md). Report vulnerabilities using [SECURITY.md](SECURITY.md).

## Development setup

Use Rust/Cargo 1.98.1 and Python 3.12+ on Linux/macOS. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
bash scripts/verify.sh
```

Downloads need network access; functional service tests use loopback fixtures.
No OpenAI key or paid API is required. The script builds and tests isolated local
packages but never publishes. Outputs/credentials are temporary or ignored.

## Project layout

| Path | Responsibility |
| --- | --- |
| `src/lib.rs`, `src/gateway.rs` | Public gateway, authentication, admission, lifecycle |
| `src/operation.rs`, `src/service.rs` | Native operations and manifest HTTP adapter |
| `src/budget.rs`, `src/queue.rs`, `src/settings.rs` | Shared accounting, selection, resource bounds |
| `src/main.rs`, `src/bin/dispatcher.rs` | Executable and optional Unix dispatcher |
| `python/ai_protocol/` | Installable SDK, connector, conformance schemas/checker |
| `examples/`, `services/` | Runnable integrations and fixture manifests |
| `tests/`, `scripts/` | Public behavior, developer journeys and artifact checks |
| `benchmarks/`, `integration/` | Historical measurements and reproduction tools |

Prefer simple Rust and small changes. Keep project prose and comments in English.
Avoid new dependencies without a concrete need. Keep permits owned by jobs;
client disconnects must not release running capacity early. Preserve owner/class
scope, permission checks before admission, queue deadlines and conservative retry
semantics. Native handlers remain read-only and nonblocking.

## Pull requests

Describe the concrete problem, resulting behavior and checks actually run. Add
meaningful tests for behavior changes, including error/cancellation cases where
relevant. Run `cargo fmt --check`, `cargo check --locked --all-targets` and relevant
tests after Rust changes; run the full verification script before requesting
review. Explain any unavailable checks. Update clients, schemas, docs and the
changelog for contract changes. Follow [compatibility rules](docs/compatibility.md).

Use focused commits and exclude generated outputs, credentials, tokens and raw
experiment results unless the change specifically requires reviewed fixtures.
Check the staged diff. Do not send tokens, private API data or machine-specific
paths in issues or logs. Include deterministic repro steps and redacted versions/
configuration. The issue and pull request templates provide a short checklist.
Contributions are accepted under the project's existing Apache-2.0 license.
