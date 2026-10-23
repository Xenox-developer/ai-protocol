# Framework preparation verification

This historical report covers preparation before the first release. For current
publication status, see [release 0.1.0](https://github.com/Xenox-developer/ai-protocol/releases/tag/v0.1.0).

Date: 2026-10-24. Scope: experimental 0.1.0 source/local-package framework,
protocol v3, manifest v1, Linux/macOS, single-process read-only operation.
No registry publication, deployment or public GitHub release was performed.

## Delivered

- Public Rust gateway and native typed handlers, host credentials/authentication,
  shared budget changes and owned lifecycle. The executable uses the same library.
- Installable adaptive Python SDK and optional Unix dispatcher connector,
  conservative typed failures and one bounded deadline/attempt budget.
- Existing HTTP adapter compatibility with separate owner-controlled upstream
  credentials; complex JSON through native handlers.
- Versioned JSON schemas, stable gateway errors, migration rules and reusable
  conformance checks. Legacy dispatcher HTTP error replies remain supported.
- Resource settings, bounded body materialization, probes/admin metrics, safe
  summaries, actionable configuration failures and bounded signal shutdown.
- Compact crate/sdist/wheel builds, isolated installations, current README,
  contribution/security/community/release guidance and GitHub templates.
- Full verification workflow on Ubuntu/macOS, including documentation and packages.
  Private vulnerability reporting is enabled on the GitHub repository.

## Local verification

`bash scripts/verify.sh` passed on macOS using Rust 1.98.1 and Python 3.12.1:

| Check | Observed result |
| --- | --- |
| Rust formatting, locked all-target check/build | Passed |
| Rust tests across all targets | 66 passed, 0 failed |
| Rust documentation example | 1 compiled, passed (`no_run`, not executed) |
| Rust public API documentation | Built without dependency docs |
| Python tests | 49 passed, 0 failed |
| Current local documentation links/anchors | 16 documents passed |
| Standalone operations | Safe configuration error, SIGTERM drain, grace expiry, JSON log checks passed |
| External Rust consumer | Separate Cargo application, real HTTP policy/native calls, zero outstanding, Ctrl+C cleanup |
| Smoke/quickstart/manifest | Passed, including three independent dispatcher clients |
| Two-service integration | Passed; final catalog/support outstanding both 0; restricted token remains 403 |
| Distribution preparation | Extracted crate all-target check; wheel built from sdist; fresh SDK install without OpenAI; real HTTP/conformance calls outside checkout |

The tests exercise custom executor/queue caps, nested/optional native input,
upstream credential separation, oversized bodies, execution deadlines, live
revocation, dynamic budgets, cancellation and safe retry boundaries. Native
handler non-success statuses cannot turn started work into an admission 429.
No Rust runtime dependency was added for framework extraction; one withdrawn
transitive macro version was updated from yoke-derive 0.8.3 to patch 0.8.4.

The package checker prints exact sizes, source commit/dirty state and SHA-256
hashes, optionally saving artifacts plus `verification.json` in a new directory.
Working-snapshot checks are explicitly marked dirty. Artifact sizes vary with
documentation/metadata; raw experiments, credentials and virtual environments
are excluded. Package preparation does not establish registry acceptance.

## Hosted verification

The full implementation at `afaa88b1a67f60f71fade35820aa5b6c5b8c4a65` passed
both Ubuntu and macOS jobs in [workflow run 40030861663](https://github.com/Xenox-developer/ai-protocol/actions/runs/40030861663).
Each job ran the complete verification script, including artifact installation.
The subsequent completion record changes documentation only and is checked by
the same workflow. Intermediate CI caught a connector compatibility regression:
valid legacy text HTTP error codes were rejected. A focused test and the two-service
scenario now cover the corrected behavior; retries were not broadened. Hosted
verification also exposed a live JSONL reader race. The reader now waits for
newline completion, including partial UTF-8 tails, and still rejects corrupt
complete records; focused tests reproduce and cover this case. The connector
also preserves the full successful 2xx status range used by the Rust dispatcher.

## Explicit limits

The external developer trial remains skipped, so no independent usability result
is claimed. Historical performance series, deployment/TLS setup on a real public
host, registry publication, Windows, distributed accounting, durable task recovery,
write idempotency and an independent security audit were not verified or added.
The reverse-proxy recipe is deployment guidance, not a tested public deployment.
See [compatibility](compatibility.md), [operations](operations.md) and
[security](../SECURITY.md) for the supported guarantees and owner responsibilities.
