# Changelog

All notable user-facing changes are recorded here. This project uses pre-1.0
Semantic Versioning for packages; protocol and manifest versions are independent.
See [compatibility](docs/compatibility.md). Registry availability is recorded on the
[release page](https://github.com/Xenox-developer/ai-protocol/releases/tag/v0.1.0).

## 0.1.0 — 2026-10-24

First experimental release of the Rust framework and Python SDK.

### Added

- Public Rust gateway library with typed native handlers, host-owned credentials,
  live authentication grants, budget updates and explicit lifecycle controls.
- Installable adaptive Python SDK, typed failures, bounded deadlines/retries,
  policy refresh and optional local dispatcher connector; optional OpenAI extra.
- Versioned machine-readable policy/operation/error/manifest contracts and a
  reusable offline/HTTP conformance checker.
- Configurable listener, queues/executors, deadlines, bounded bodies, separate
  upstream authentication, health/readiness, admin metrics, safe request logging
  and bounded SIGINT/SIGTERM shutdown.
- Compact crate/sdist/wheel preparation with isolated Rust/Python consumer checks,
  contribution/security/community guidance and release procedure.

### Changed

- Standalone gateway and existing examples use the shared public library/SDK.
- Gateway errors use stable JSON codes and explicit execution certainty while
  preserving safe-retry statuses and Retry-After behavior.
- Native/mapped execution includes response materialization under one deadline;
  oversized responses and native handler failures fail conservatively.

- Locked transitive `yoke-derive` updated from withdrawn 0.8.3 to patch 0.8.4.

### Preserved

- Default catalog and second-service manifests, routes and launch commands.
- Protocol v3 owner/class budgets across tokens, dynamic shrink/growth, weighted
  3:1/FIFO selection, bounded queue waiting and no replay of unknown outcomes.
- Legacy Python entry points and manifest version 1 when format_version is absent.

Earlier catalog, scheduling, dispatcher and second-service experiment history is
available in Git and the [historical runbook](docs/history/README-before-framework.md).
