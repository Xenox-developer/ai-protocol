# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Xenox-developer/ai-protocol/security/advisories/new).
Private reporting is enabled for this repository. Describe the affected commit/
package version, reproduction, impact and proposed mitigation if known. Use
synthetic credentials and minimized fixtures; never send live tokens or private
customer data. Do not disclose exploit details through a public issue before
maintainers have had an opportunity to investigate.

If the private reporting UI is unavailable, open a
[private contact request](https://github.com/Xenox-developer/ai-protocol/issues/new?title=Private%20security%20contact%20request)
without vulnerability details or secrets. Maintainers will arrange a suitable
channel. Handling is best-effort; no response SLA or bounty is promised.

## Supported scope

This is an experimental pre-1.0 project. Security fixes target the latest reviewed
main branch and any subsequently announced supported release. No older release
or long-term support promise exists. There has been no independent security audit.

The gateway authenticates bearer credentials, checks operation grants before
admission, bounds queue/execution/body resources and uses one shared counter per
owner/class in one process. The optional dispatcher uses private Unix sockets.
Client-supplied headers cannot select a scheduling class. Manifests define fixed
upstream routes; parameters cannot select another origin. Redirects are disabled.
The upstream bearer credential is separate from caller/admin credentials.

## Deployment responsibilities

- Terminate public HTTPS at a configured reverse proxy; keep admin routes private.
- Supply read-only, least-privilege credentials from a secret store. Environment
  variables are a convenience, not a credential management system. Static token
  rotation requires restart; embedded applications may use their own live verifier.
- Treat native handlers, schemas, manifests and authentication callbacks as
  trusted application code. Keep schemas and server validation consistent; bound
  handler allocations and avoid blocking Tokio. Do not run untrusted handlers.
- Account for execution slots × response bound and queued input buffers. Configure
  proxy/process resource limits and disable proxy retries of operation POSTs.
- A timeout, restart or disconnect does not prove remote execution did not happen.
  Unknown outcomes must not be replayed automatically. This release defines reads
  only; it has no write idempotency or durable task recovery protocol.
- Do not expose multiple gateway replicas as one shared budget authority. Budgets,
  revisions, metrics and queues are in memory and reset on restart.

The framework omits credentials, paths and bodies from built-in request summaries;
application, upstream, reverse-proxy and benchmark logs need their own review.
See [operations](docs/operations.md) and [protocol guarantees](docs/protocol.md).
