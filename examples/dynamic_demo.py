"""Run a paid-API-free 5 -> 2 -> 5 demonstration on private loopback instances.

Build first: cargo build --locked --bins
Run: python3 examples/dynamic_demo.py
The real catalog handler is gated to make outstanding work observable.
"""

from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import threading
import time

import httpx
from catalog_service import Handler

ROOT = Path(__file__).resolve().parents[1]


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    started = queue.Queue()
    release = threading.Semaphore(0)
    stopped = threading.Event()

    class ControlledCatalog(Handler):
        def do_GET(self):
            started.put(None)
            if not release.acquire(timeout=10):
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
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            gateway_port = probe.getsockname()[1]
        environment["GATEWAY_PORT"] = str(gateway_port)
        environment["CATALOG_PORT"] = str(catalog.server_address[1])
        gateway = subprocess.Popen([str(ROOT / "target/debug/ai-protocol")], cwd=ROOT,
                                   env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stack.callback(stop_process, gateway)
        http = stack.enter_context(httpx.Client(trust_env=False, timeout=3))
        base = f"http://127.0.0.1:{gateway_port}"
        deadline = time.monotonic() + 10
        while True:
            if gateway.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Gateway did not become ready; build the binaries first")
            try:
                if http.get(base + "/agent-policy").status_code == 401:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.05)

        client_environment = environment.copy()
        for name in names[1:]:
            client_environment.pop(name, None)
        client_environment.update(LOAD_AGENT_TASKS="60", LOAD_DEADLINE_SECS="60")
        load = subprocess.Popen([str(ROOT / "target/debug/load")], cwd=ROOT,
                                env=client_environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        stack.callback(load.stdout.close)
        stack.callback(stop_process, load)
        events = queue.Queue()

        def read_output():
            for line in load.stdout:
                if any(environment[name] in line for name in names):
                    events.put({"event": "credential_leak"})
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.put(event)
            events.put({"event": "exited"})

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()

        def wait_event(predicate):
            deadline = time.monotonic() + 20
            while True:
                event = events.get(timeout=max(0.01, deadline - time.monotonic()))
                if event["event"] in ("exited", "credential_leak"):
                    raise RuntimeError("Load client ended unexpectedly or emitted credentials")
                print(json.dumps(event), flush=True)
                if predicate(event):
                    return event
                if time.monotonic() >= deadline:
                    raise RuntimeError("Expected client policy transition was not observed")

        def change(maximum):
            response = http.patch(base + "/admin/principals/demo-owner/limits",
                                  headers={"Authorization": f"Bearer {environment['ADMIN_TOKEN']}"},
                                  json={"max_outstanding": maximum})
            response.raise_for_status()
            result = response.json()
            print(json.dumps({"event": "admin_update", "policy_revision": result["policy_revision"],
                              "limit": result["limits"]["max_outstanding"],
                              "server_outstanding": result["outstanding"]}), flush=True)
            return result

        # Hold the first five catalog requests until both server and client see 2.
        for _ in range(5):
            started.get(timeout=10)
        reduced = change(2)
        assert reduced["outstanding"] == 5 and reduced["policy_revision"] == 2
        event = wait_event(lambda item: item["event"] == "policy" and item["policy_revision"] == 2)
        assert event["client_active"] == 5 and event["limit"] == 2

        def paced_completion():
            while not stopped.wait(0.1):
                release.release()

        threading.Thread(target=paced_completion, daemon=True).start()
        wait_event(lambda item: item["event"] == "policy" and item["limit"] == 2
                   and item["client_active"] == 2 and item["server_outstanding"] <= 2)
        raised = change(5)
        assert raised["policy_revision"] == 3
        wait_event(lambda item: item["event"] == "policy" and item["policy_revision"] == 3
                   and item["client_active"] == 5)
        completed = wait_event(lambda item: item["event"] == "completed")
        assert completed["successful"] == 60 and completed["errors"] == 0
        assert load.wait(timeout=5) == 0
        reader.join(timeout=5)
        response = http.get(base + "/agent-policy",
                            headers={"Authorization": f"Bearer {environment['AGENT_TOKEN_1']}"})
        response.raise_for_status()
        assert response.json()["outstanding"] == 0
        print("PASS: 60/60 tasks; 5 -> 2 -> 5; final outstanding=0; no paid API calls", flush=True)


if __name__ == "__main__":
    main()
