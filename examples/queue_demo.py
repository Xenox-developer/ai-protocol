"""Demonstrate mixed-class progress and a queue timeout using private instances.

Build: cargo build --locked --bins
Run: python3 examples/queue_demo.py
No paid API is used. The controlled catalog records every upstream invocation.
"""

import asyncio
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
from urllib.parse import parse_qs, urlparse

import httpx
from catalog_service import Handler
from dynamic_demo import stop_process

ROOT = Path(__file__).resolve().parents[1]


async def main():
    holds_started = threading.Semaphore(0)
    release_holds = threading.Event()
    release_work = threading.Semaphore(0)
    stopped = threading.Event()
    seen = set()
    seen_lock = threading.Lock()

    class ControlledCatalog(Handler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query).get("query", [""])[0]
            with seen_lock:
                seen.add(query)
            if query.startswith("hold-"):
                holds_started.release()
                ready = release_holds.wait(timeout=4)
            else:
                ready = release_work.acquire(timeout=4)
            if not ready:
                self.send_error(503)
                return
            super().do_GET()

        def log_message(self, *_):
            pass

    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("TEST_429", None)
    names = ("AGENT_TOKEN_1", "AGENT_TOKEN_2", "PRODUCT_ONLY_TOKEN",
             "INTERACTIVE_TOKEN", "OTHER_AGENT_TOKEN", "ADMIN_TOKEN")
    for name in names:
        environment[name] = secrets.token_urlsafe(32)
    with ExitStack() as stack:
        catalog = ThreadingHTTPServer(("127.0.0.1", 0), ControlledCatalog)
        stack.callback(catalog.server_close)
        threading.Thread(target=catalog.serve_forever, daemon=True).start()
        stack.callback(catalog.shutdown)
        stack.callback(stopped.set)
        stack.callback(release_holds.set)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            gateway_port = probe.getsockname()[1]
        environment.update(GATEWAY_PORT=str(gateway_port), CATALOG_PORT=str(catalog.server_address[1]),
                           INTERACTIVE_MAX_WAIT_MS="500", AGENT_MAX_WAIT_MS="10000")
        gateway = subprocess.Popen([str(ROOT / "target/debug/ai-protocol")], cwd=ROOT,
                                   env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stack.callback(stop_process, gateway)
        base = f"http://127.0.0.1:{gateway_port}"
        async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
            for _ in range(100):
                if gateway.poll() is not None:
                    raise RuntimeError("Private gateway failed to start; build the binaries first")
                try:
                    if (await http.get(base + "/agent-policy")).status_code == 401:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Private gateway did not become ready")

            def headers(name):
                return {"Authorization": f"Bearer {environment[name]}"}

            async def post(name, query):
                return await http.post(base + "/search", headers=headers(name), json={"query": query})

            async def policy(name):
                response = await http.get(base + "/agent-policy", headers=headers(name))
                response.raise_for_status()
                return response.json()

            response = await http.patch(base + "/admin/principals/demo-owner/limits",
                                        headers=headers("ADMIN_TOKEN"), json={"max_outstanding": 40})
            response.raise_for_status()
            interactive = await policy("INTERACTIVE_TOKEN")
            assert interactive["queue"]["max_wait_ms"] == 500
            holds = [asyncio.create_task(post("AGENT_TOKEN_1", f"hold-{index}")) for index in range(10)]
            for _ in range(10):
                assert await asyncio.to_thread(holds_started.acquire, timeout=3)
            # No completions or new requests occur while this single waiter expires.
            expired = await post("INTERACTIVE_TOKEN", "must-not-start")
            assert expired.status_code == 503
            assert expired.json() == {"error": {"code": "queue_timeout", "execution": "not_started"}}
            assert expired.headers["Retry-After"] == "1"
            with seen_lock:
                assert "must-not-start" not in seen
            assert (await policy("INTERACTIVE_TOKEN"))["outstanding"] == 0
            print(json.dumps({"event": "queue_timeout", "execution": "not_started",
                              "upstream_called": False, "interactive_outstanding": 0}), flush=True)
            release_holds.set()
            for result in await asyncio.gather(*holds):
                assert result.status_code == 200

            def pace_completions():
                while not stopped.wait(0.01):
                    release_work.release()

            threading.Thread(target=pace_completions, daemon=True).start()
            completed = {"interactive": 0, "agent": 0}

            async def stream(kind, worker):
                token = "INTERACTIVE_TOKEN" if kind == "interactive" else "AGENT_TOKEN_1"
                for number in range(10):
                    result = await post(token, f"{kind}-{worker}-{number}")
                    assert result.status_code == 200, f"Unexpected {result.status_code} in {kind} stream"
                    completed[kind] += 1
                    if completed[kind] % 20 == 0:
                        print(json.dumps({"event": "progress", **completed}), flush=True)

            await asyncio.wait_for(asyncio.gather(
                *(stream("interactive", worker) for worker in range(8)),
                *(stream("agent", worker) for worker in range(12)),
            ), timeout=20)
            assert completed == {"interactive": 80, "agent": 120}
            for name in ("AGENT_TOKEN_1", "OTHER_AGENT_TOKEN", "INTERACTIVE_TOKEN"):
                assert (await policy(name))["outstanding"] == 0
            with seen_lock:
                assert "must-not-start" not in seen
                assert len(seen) == 210  # Ten held requests plus 200 stream requests.
            print(json.dumps({"event": "completed", **completed, "queue_timeouts": 1,
                              "expired_upstream_calls": 0, "final_outstanding": 0}), flush=True)
            print("PASS: both classes advanced; expired task never reached upstream; budgets drained", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
