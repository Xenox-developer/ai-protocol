# Preparation for the first external developer trial

Date: 2026-09-23. Project name remains AI Protocol. This stage adds onboarding,
examples and verification infrastructure; it adds no protocol capability and
changes no gateway/dispatcher production code or dependency requirements. Existing
uncommitted work and historical experiment results are preserved. No commit,
push, release, paid API call or full load-series rerun was performed.

## What was prepared

- [Short Unix quickstart](../QUICKSTART.md): pinned/tested tool versions,
  dependency installation, a first policy and successful call, three independent
  client processes through a dispatcher, and stopping owned processes.
- `examples/quickstart.py`: free ports, temporary credentials, explicit numbered
  results, normal cleanup and optional `--keep-running` for manual exploration.
  Its private agent file contains just one agent credential; it never reads `.env`.
- [Manifest template](../../services/template.json) plus the
  [exercise and field explanations](EXERCISE.md): connect a new read operation
  through the existing manual POST JSON → GET query mapper without Rust edits.
  `tests/trial_manifest.py` verifies the template and one sample solution against
  the local training API, including a restricted token.
- Clear responsibility split: service owner configures operations/API mappings,
  permissions and budgets; agent developer uses discovery/client credentials;
  an owner of multiple agents optionally runs the local dispatcher.
- [Feedback form](FEEDBACK.md): startup success, time to first call, confusing
  instructions, missing transformations and exercise/cleanup observations.
- `rust-toolchain.toml` pins Rust 1.98.1 with rustfmt. Python 3.12 is the trial
  recipe; existing pinned direct Python requirements and Cargo.lock are retained.
- `scripts/verify.sh`: formatting, all-target check/build/tests, Python unit tests,
  existing smoke test, quickstart, manifest exercise and the two-service demo.
- `.github/workflows/checks.yml`: Ubuntu/macOS jobs, Python 3.12, pinned Rust,
  read-only repository permissions, 20-minute timeout and the same verify script.
  No repository/service secrets are required. Only tool/dependency installation
  uses external downloads; the test services use loopback and LLM calls are mocked.
- `scripts/check_clean.py`: export the intended repository working snapshot,
  create a fresh Python environment and Cargo download/build directories, then
  run the verify script with project environment settings removed.
- README links the trial path first and states the experimental limitations.

## Automatically checked here

The clean run succeeded on **macOS arm64**, Rust/Cargo **1.98.1**, Python
**3.12.1**. It copied **1,876** tracked and nonignored untracked repository files
into a newly created `/tmp` checkout. Including intended uncommitted files is
necessary because the current implementation has not been committed/published.
This is not a check of `git archive HEAD` or of an externally available commit.

The exported snapshot contained no `.git`, `target`, `.venv`, local `.env` files
or parent-directory files. No original checkout binaries or virtualenv packages
were used. The process used a new HOME, CARGO_HOME and Python venv; application
credentials/settings, PYTHONPATH, target overrides and API keys were not inherited.
Only installed tool locations (PATH and rustup's toolchain directory) were carried
over. Rust toolchain availability was resolved by rustup; registry dependencies
and Python packages were downloaded afresh. Runtime paths are computed from the
copied checkout, not the author's project location. The external
CODEX_PROJECT_CONTEXT.md and ignored AGENTS.md were not needed to run the project.

| Check in the clean checkout | Actual result |
|---|---|
| `cargo fmt --check` | Passed |
| `cargo check --locked --all-targets` | Passed |
| `cargo build --locked --bins` | Passed, new build outputs |
| `cargo test --locked --all-targets` | 52 test executions passed: 28 gateway, 13 dispatcher, 8 load, 3 benchmark client |
| `python -m unittest discover -s tests -v` | 29 passed; LLM-related unit tests mocked |
| `python tests/smoke.py` | Passed: actual discovery/catalog calls, permissions, Retry-After and failed-discovery deadline |
| `python examples/quickstart.py` | First direct call succeeded; three separate dispatched clients succeeded; outstanding zero; owned children and temporary state removed |
| `python tests/trial_manifest.py` | Template and newly described `find_records` operation succeeded without Rust edits; restricted token received 403; outstanding zero |
| `python examples/service_integration_demo.py --output <new-temp-path>` | Passed: two services, restricted credentials, independent budgets and final outstanding zero for both |

Outside the clean run, the optional quickstart mode was also driven automatically:
wait for `--keep-running`, verify the agent file is mode 0600, fetch policy using
that one token, send SIGINT to the owned parent and check successful exit, removed
credentials/socket and unreachable owned gateway. This simulates Ctrl+C; it is
not evidence of a person successfully following the instructions. Python syntax,
Bash script syntax and `git diff --check` also passed.

Evidence: [clean-check.json](../../integration/results/first-trial/clean-check.json)
contains input file hashes, tool versions, installed Python package versions and
isolation flags; [checks.txt](../../integration/results/first-trial/checks.txt)
contains the actual clean-run output. These evidence files and this final report
were written after the run, so they are not themselves in that input snapshot.
Production code, template, helper scripts, workflow and instructional documents
used by the run can be matched against the input hashes. Prior reports and raw
load results remain unchanged; no new performance conclusion is made.

Reproduction from the repository root (choose a new report path):

```sh
python3 scripts/check_clean.py --report /tmp/ai-clean-next.json
```

This intentionally re-downloads dependencies and rebuilds instead of using the
project's target directory. It removes its temporary checkout after completion.
The lighter everyday/CI command, with dependencies installed, is:

```sh
bash scripts/verify.sh
```

## What remains unverified or needs an external person

- **Hosted CI has not run.** The Ubuntu/macOS workflow is prepared, but no push or
  GitHub action dispatch was performed. Local macOS success is not Linux proof.
- Installation on another person's fresh OS, differing package/network policies,
  native tool setup and the hosted Python 3.12 patch version remain to be checked.
  Transitive Python dependencies are not fully locked; the clean report records
  the versions resolved for this run. No broad minimum-version matrix is claimed.
- A first-time developer must independently follow the quickstart and measure
  time to first call, report unclear steps, and verify cleanup from their terminal.
- A service owner must try the exercise or their own read API without Rust edits
  and identify API transformations that do not fit. Our sample solution proves a
  particular mapping works, not that onboarding is intuitive or universal.
- The role split, token distribution instructions and dispatcher setup need human
  feedback. Neither usability nor reduced integration effort is established by
  the author's automated run.
- A person who later shares the source must include the intended new files or
  commit them first. The current remote HEAD alone may lack the prepared work.
  This task deliberately did not create a commit, archive publication or release.

## Limitations presented to the trial participant

All scheduling/budget/job state is in memory. Budget accounting is confined to
one gateway process; there is no coordination across replicas. The dispatcher
coordinates only connected agents for one service/owner and is a single point of
failure. Recovery after restart is not guaranteed; unknown execution outcomes
are not automatically replayed. Policy is a ceiling, not a slot reservation.

API mapping is manual: required flat strings/nonnegative u64 integers in gateway
POST JSON become fixed upstream GET query parameters with unchanged JSON output.
Unsupported transformations must be reported rather than silently invented.
Unix sockets require macOS/Linux; native Windows is outside the current trial.
