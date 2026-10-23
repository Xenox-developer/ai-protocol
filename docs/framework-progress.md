# Framework release preparation

This is the implementation checklist for the authorized staged framework task.
Git commits are pushed incrementally. Package publication, deployments and a
public release are outside the task. The external developer trial remains skipped.

- [x] Public Rust library, native handlers and lifecycle, with external consumer.
- [x] Installable adaptive Python SDK and dispatcher connector.
- [x] Practical service integration and owner-controlled upstream credentials.
- [x] Machine-readable contracts, structured errors and compatibility checks.
- [x] Operational configuration, bounded responses, shutdown, metrics and auth integration.
- [x] Compact Rust/Python distributions and isolated installation checks.
- [x] Open-source README, contribution/security/versioning/operations documentation.
- [ ] Final developer-journey verification and hosted Linux/macOS checks.

Supported release scope: one in-memory gateway process per service, read-only
operations, Linux/macOS dispatcher, HTTPS through a reverse proxy. Distributed
coordination, durable recovery, writes, native Windows dispatcher, a UI and API
importers remain deferred. Passing checks is not a production-security claim.
