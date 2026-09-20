# AI Protocol

An experimental HTTP protocol for communication between services and AI agents.
The Rust server publishes available operations and a concurrency limit,
prioritizes human requests, and forwards operations to a Python catalog service.
Agent clients discover operations through `/agent-policy`,
validate parameters against JSON Schema, and handle `429` responses with `Retry-After`.

This is a local MVP, not an established public standard.

## Structure

```text
ai-protocol/
├── README.md
├── LICENSE
├── .gitignore
├── Cargo.toml
├── Cargo.lock
├── requirements.txt
├── src/
│   ├── main.rs
│   └── bin/
│       ├── load.rs
│       └── load_plain.rs
├── examples/
│   ├── catalog_service.py
│   ├── discover_client.py
│   └── llm_client.py
├── docs/
│   └── protocol.md
└── benchmarks/
    ├── README.md
    └── results/
```

## Getting started

You need Rust/Cargo with edition 2024 support and Python 3.10+.
Run all commands from the repository root.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cargo build --locked --bins
```

Start the catalog service in the first terminal:

```sh
.venv/bin/python examples/catalog_service.py
```

Start the server in a second terminal:

```sh
cargo run --locked --bin ai-protocol
```

The catalog listens on `127.0.0.1:4000`, and the server on `127.0.0.1:3000`.
In a third terminal, run the interactive operation discovery client:

```sh
.venv/bin/python examples/discover_client.py
```

Choose operation `1` and enter `{"query":"boots"}`, or operation `2`
and `{"id":1}`. The client displays the catalog's JSON response.

To let a language model choose an operation:

```sh
.venv/bin/python examples/llm_client.py
```

The client uses `OPENAI_API_KEY` if the environment variable is set; otherwise,
it prompts for the key without displaying the input. Example task: `Find all boots`.
The current code uses `gpt-4.1-mini`; the client executes at most one
operation and prints its JSON result. Calling the model requires OpenAI API access.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/agent-policy` | Version `2`, `max_in_flight`, and operation descriptions |
| POST | `/search` | Substring search: `{"query":"boots"}` |
| POST | `/product` | Retrieve a product: `{"id":1}` |

```sh
curl http://127.0.0.1:3000/agent-policy
curl -X POST http://127.0.0.1:3000/search \
  -H 'Content-Type: application/json' \
  -H 'X-Client-Type: agent' \
  -d '{"query":"boots"}'
```

To test retries, start the server with `TEST_429=1`:

```sh
TEST_429=1 cargo run --locked --bin ai-protocol
```

The first agent request receives `429` and `Retry-After: 2` before the operation runs.
Stop any existing server before starting another one on the same port.

See the [protocol contract](docs/protocol.md) for details.
Load generators and saved measurements are described in [benchmarks](benchmarks/README.md).

## Limitations

Queues are stored in memory. `X-Client-Type` is a voluntary client label,
not authentication. The human queue is unbounded; strict priority
may delay agents under sustained human traffic.
The client limit is not a global server quota.

## License

Copyright (c) 2026 Nikita Chindin. Licensed under [Apache-2.0](LICENSE).
