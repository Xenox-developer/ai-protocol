# AI Protocol — current v2 implementation

An experimental HTTP contract for the local server in `src/main.rs`.

## Operations

| Method and path | Request | Successful response |
| --- | --- | --- |
| `GET /agent-policy` | No body | `version: 2`, `max_in_flight: 10`, `operations` |
| `POST /search` | `{"query":"boots"}` | `{"products":[...]}` |
| `POST /product` | `{"id":1}` | `{"product":{...}}` or `{"product":null}` |

The policy describes each operation using the fields `name`, `description`, `method`,
`path`, and `input_schema`. Parameters are sent as a JSON body with
`Content-Type: application/json`. Search performs a case-insensitive substring
match against English product names; an empty string returns the entire catalog.
`id` is a non-negative integer within the Rust `u64` range.

Agents send `X-Client-Type: agent`. A missing header or a different value
routes the request to the human queue. This is not authentication.
The server selects human tasks first and does not interrupt tasks already running.
The total execution limit is 10 tasks; the waiting agent queue is limited to 32.
The client limit is not a shared quota across all clients.

When the agent queue is full, the server returns `429` and `Retry-After: 1`
before accepting the task. `TEST_429=1` enables a single test rejection with
`Retry-After: 2`. The client may retry after the specified delay,
but must limit the number of attempts. The examples allow at most 5 attempts.
Other errors are not retried automatically.

The server forwards operations to the local catalog at `127.0.0.1:4000`:
`GET /products/search?query=...` or `GET /products/get?id=...`.
The request timeout is 5 seconds. Catalog unavailability, an error response,
or a failure to read the response body results in `502`; a timeout while
sending the request results in `504`.
If the result channel is lost, the server may return `500`.

The Python clients validate parameters against the published JSON Schema and
run sequentially, so they stay within any positive concurrency limit.
The Rust `load` client uses a shared semaphore and supports version `2`.
`load_plain` provides a comparison without a client-side policy.
The current server does not expose `/work`.

Startup commands are in the [README](../README.md); measurements are described
in [benchmarks/README.md](../benchmarks/README.md).

---

## Archived description of the previous experiment

The original document is translated below without correcting its historical claims.
Although its heading identifies version `2`, its sections describe the earlier
v1 contract with `/work` and a synthetic delay. For current operations and
the current version, use the description above. The verification results below
refer to the previous experiment.

# AI Protocol — experimental MVP contract

Document revision: 0.1. Message version: `2`.

Status: a local experiment, not a public standard or a production-ready implementation.

This document describes the server and client code developed during the conversation. It does not assume access to subsequent local source changes.

## 1. Purpose

The service tells an agent client how much concurrency is allowed. The client limits the number of in-flight requests and handles temporary rejection before a task is accepted. The server prioritizes interactive requests.

The words “must” and “must not” indicate requirements of this experimental contract. Specific settings for the current implementation are listed separately in section 7.

## 2. Transport and service address

The client is given a base URL in advance, such as `http://127.0.0.1:3000`.

The local MVP uses HTTP. HTTPS is not configured in the current experiment; TLS does not change the paths and fields described here. Automatic service discovery is not available.

| Method and path | Purpose |
| --- | --- |
| `GET /agent-policy` | Retrieve the service policy |
| `POST /work` | Execute the test operation |

## 3. Retrieving the policy

Before starting agent tasks, the client must successfully retrieve the policy:

```http
GET /agent-policy HTTP/1.1
Host: 127.0.0.1:3000
```

A successful response has status `200`, content type `application/json`, and the following body:

```json
{"version":1,"max_in_flight":10}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | Integer | Contract version; this client supports only `1` |
| `max_in_flight` | Positive integer | Maximum number of concurrent in-flight agent HTTP attempts from one client instance to this service |

The client must validate the presence and types of required fields, confirm that the version is supported, and ensure that the limit is positive. Unknown additional fields should be ignored if the version is supported. If policy retrieval or validation fails, agent traffic must not start; the error must be reported to the calling code.

The limit must be shared by all tasks from one client instance that access this service. A separate semaphore must not be created for each request. Requests waiting for a client-side permit are not yet HTTP attempts.

This is an agreement with the client, not server capacity reserved for it. The limit is not a global quota across all clients. The current implementation has no client identity or server-side enforcement of individual client quotas.

The policy is read at startup. Periodic refresh, expiration, and changes to the limit during execution are not yet defined. Changing the value of `max_in_flight` does not require a version change. An incompatible change to the meaning of fields requires a new version.

## 4. Executing an operation

The agent client must send:

```http
POST /work HTTP/1.1
Host: 127.0.0.1:3000
X-Client-Type: agent
Content-Length: 0
```

The interactive load generator uses `X-Client-Type: human`.

HTTP header names are case-insensitive. The implementation compares the value `agent` exactly; a missing header or any other value routes the request to the human queue. This is demo server behavior, not a reliable way to identify a human.

The test operation has an empty request body. Business operation parameters, their schemas, and automatic operation discovery are not yet available.

Successful response:

```http
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8

Done
```

Success means that the server has completed the test operation. To measure the full duration of an HTTP attempt, the client must read the entire response body.

The test operation is an asynchronous wait of approximately 100 ms. It does not model actual computation or data modification.

## 5. Overload and retries

If the agent queue is full, the server rejects the request before enqueueing or executing it:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 1
Content-Type: text/plain; charset=utf-8

Agent queue is full
```

Under this contract, a `429` response from `/work` guarantees that the attempt was not accepted for execution. This is a requirement of this service, not a universal guarantee of HTTP APIs. Retrying such an attempt is allowed.

The server must provide `Retry-After` as a non-negative integer number of seconds. HTTP dates are not used in this experimental profile. The client must wait at least the specified duration before retrying. The response body is diagnostic: decisions are based on the status and header, not the string `Agent queue is full`.

The client must have a finite attempt budget and respect the concurrency limit during retries. Once the budget is exhausted, the task is considered unsuccessful; `429` must not be treated as successful completion.

The current Rust client:

- Makes at most 5 attempts, including the initial attempt.
- Uses 1 second if `Retry-After` is missing or cannot be parsed.
- Holds the client-side permit while waiting to retry; this is stricter than limiting only active HTTP attempts.
- Does not add random jitter to retry delays.
- Automatically retries only agent requests that receive `429`.

Network errors, timeouts, and other HTTP error statuses are not retried automatically. If a response is lost, the operation's outcome may be unknown. Idempotency keys, deduplication, and exactly-once execution guarantees are not implemented.

If the result channel is unavailable, the handler may return `500`. This does not guarantee that the operation was never executed and does not justify a safe automatic retry.

## 6. Priority scheduling

The server maintains two FIFO queues. When selecting the next task, it checks the human queue first, then the agent queue. Tasks already running are not interrupted.

All tasks share the same execution capacity. Agents can use all available slots when there are no humans waiting in the queue. Priority does not guarantee zero wait time for humans: all slots may be occupied by running tasks.

Queues, the scheduler, semaphores, and the programming language are implementation details. A compatible client must not depend on their types or internal structure.

## 7. Current test setup

| Setting | Value |
| --- | --- |
| Total limit on running tasks | 10 |
| Waiting agent queue limit | 32; running tasks are excluded |
| Advertised client limit | 10; a value of 5 was also tested |
| Valid limit range in the Rust client | 1–1000; a local client safety bound |
| Timeout for one HTTP attempt | 30 seconds |
| Overall task deadline, including client-side waiting and retries | Not set |
| Queue storage | In memory; state is lost on restart |
| Human queue | Unbounded |

The `TEST_429` startup flag enables a single artificial rejection of the first agent request: `429` with `Retry-After: 2`. This flag is a testing tool, not part of the network contract. Without the flag, a full queue can still produce normal `429` responses.

## 8. Independent client algorithm

1. Read the base URL from configuration.
2. Request `/agent-policy` and validate the version and limit.
3. Create a shared concurrency limiter for agent requests to this service.
4. When a logical task is created, record its creation time and wait for a permit.
5. Send `POST /work` with `X-Client-Type: agent`.
6. On `200`, read the entire response and complete the task successfully.
7. On `429`, while attempts remain, read `Retry-After`, wait for the specified delay, and retry.
8. On another error or when the attempt budget is exhausted, fail the task.
9. Release the client-side permit whenever the task finishes, regardless of the outcome.

This algorithm can be implemented in Python, TypeScript, or another language without importing the Rust server code.

## 9. Compatibility checks

| Check | Expected result |
| --- | --- |
| The server advertises a limit of 10 | The client allows at most 10 in-flight agent attempts |
| The server advertises a limit of 5; the client is restarted without code changes | The client applies 5 |
| An unsupported version, such as 2 | The client does not start agent traffic and reports an error |
| A limit of 0 | The client reports an error instead of waiting indefinitely on a semaphore |
| A new optional field with version 1 | The client continues to work |
| A single `429` with `Retry-After: 2` | The retry starts no sooner than 2 seconds after the response is received; the task then completes |
| Repeated `429` responses | The client stops retrying when its finite attempt budget is exhausted |
| A timeout or connection failure | The client reports an error and does not retry automatically |

Successful requests, limits of 10 and 5, priority scheduling, rejection on queue overflow, and a single retry after a two-second wait were checked during the conversation. The remaining rows are planned checks, not completed results. Exact compliance with the concurrent attempt limit was not instrumented separately.

## 10. Measuring the effect

Comparisons should use the same server, the same logical task creation times, and the same task counts. Track successful and unsuccessful tasks separately.

The full task duration includes client-side waiting, requests, server-side waiting, and retry delays. It is also useful to measure the number of HTTP attempts, the number of `429` responses, the maximum server queue length, and the time needed to complete the entire set of tasks.

The current generator measures latency from the start of the asynchronous task, before `send_request`, until that task completes. This includes the client-side semaphore wait and retries, but excludes any delay in starting relative to the planned time. Its p95 is calculated only for successful tasks. Comparing p95 without the error count is therefore invalid.

## 11. MVP limitations

- `X-Client-Type` can be spoofed; it is a label, not authentication or authorization.
- There is no client identity, delegated permissions, or global budget across multiple clients.
- There is no capacity reservation based on the advertised limit or policy refresh during execution.
- There is no durable task storage, task identifiers for status queries, or crash recovery.
- Work is not guaranteed to be canceled when a client disconnects.
- Strict priority can starve agents under sustained human traffic.
- The client's waiting task queue and the server's human queue remain unbounded.
- Handling of extremely large `Retry-After` values, overall deadlines, and protection against synchronized retry bursts is incomplete.
- Real operations, HTTPS, independent implementations, and production security require further work.

## 12. Existing mechanisms used

This is an experimental contract over HTTP. It reuses standard HTTP methods, statuses, and the `Retry-After` header. The `/agent-policy` path, policy schema, and `X-Client-Type` convention are specific to this contract.

- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)
- [429 Too Many Requests — RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html)

The existence of this document does not imply that all mechanisms are novel or that the protocol is recognized as a standard. Its purpose is to give other implementations unambiguous rules for interacting with the current test setup.
