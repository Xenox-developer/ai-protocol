"""Exercise the real local catalog, gateway, and discovery client with ephemeral credentials.

Run after cargo build --locked --bins. Uses dynamically selected loopback ports.
"""

from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running(command, environment):
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def ready(process, client, url, expected):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("A smoke-test service exited during startup")
        try:
            if client.get(url).status_code == expected:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise RuntimeError("Smoke-test service did not become ready")


def main():
    with socket.socket() as gateway_probe, socket.socket() as catalog_probe:
        gateway_probe.bind(("127.0.0.1", 0))
        catalog_probe.bind(("127.0.0.1", 0))
        gateway_port = gateway_probe.getsockname()[1]
        catalog_port = catalog_probe.getsockname()[1]
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("ADMIN_TOKEN", None)
    names = ("AGENT_TOKEN_1", "AGENT_TOKEN_2", "PRODUCT_ONLY_TOKEN", "INTERACTIVE_TOKEN", "OTHER_AGENT_TOKEN")
    for name in names:
        environment[name] = secrets.token_urlsafe(32)
    environment["TEST_429"] = "1"
    environment["GATEWAY_PORT"] = str(gateway_port)
    environment["CATALOG_PORT"] = str(catalog_port)
    base = f"http://127.0.0.1:{gateway_port}"
    with ExitStack() as stack:
        client = stack.enter_context(httpx.Client(trust_env=False, timeout=10))
        catalog = stack.enter_context(running([sys.executable, "examples/catalog_service.py"], environment))
        gateway = stack.enter_context(running([str(ROOT / "target/debug/ai-protocol")], environment))
        ready(catalog, client, f"http://127.0.0.1:{catalog_port}/products/search", 200)
        ready(gateway, client, base + "/agent-policy", 401)
        start = time.monotonic()
        result = subprocess.run([sys.executable, "examples/discover_client.py"], cwd=ROOT, env=environment,
                                input='1\n{"query":"sneakers"}\n', text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, "Discovery client failed"
        assert "White sneakers" in result.stdout
        assert "wait 2 seconds" in result.stdout
        assert time.monotonic() - start >= 2
        for name in names:
            assert environment[name] not in result.stdout + result.stderr
        headers = {"Authorization": f"Bearer {environment['AGENT_TOKEN_1']}"}
        for query, count in [("", 3), ("boots", 2), ("SNEAKERS", 1)]:
            response = client.post(base + "/search", headers=headers, json={"query": query})
            assert response.status_code == 200
            assert len(response.json()["products"]) == count
        product_headers = {"Authorization": f"Bearer {environment['PRODUCT_ONLY_TOKEN']}"}
        response = client.post(base + "/product", headers=product_headers, json={"id": 2})
        assert response.json()["product"]["name"] == "Brown boots"
        assert client.post(base + "/product", headers=headers, json={"id": 999}).json() == {"product": None}
        assert client.post(base + "/search", headers=product_headers, json={"query": ""}).status_code == 403
        for name, principal, kind, maximum in [
            ("AGENT_TOKEN_1", "demo-owner", "agent", 5),
            ("AGENT_TOKEN_2", "demo-owner", "agent", 5),
            ("OTHER_AGENT_TOKEN", "other-owner", "agent", 5),
            ("INTERACTIVE_TOKEN", "demo-owner", "interactive", 10),
        ]:
            response = client.get(base + "/agent-policy", headers={"Authorization": f"Bearer {environment[name]}", "X-Client-Type": "human"})
            assert response.headers["Cache-Control"] == "no-store"
            policy = response.json()
            assert (policy["version"], policy["principal_id"], policy["client_class"], policy["limits"]["max_outstanding"]) == (3, principal, kind, maximum)
        # A permanently failing discovery must stop at the workload deadline.
        failed_environment = environment.copy()
        failed_environment.update(AGENT_TOKEN_1="unknown-test-token", LOAD_AGENT_TASKS="1", LOAD_DEADLINE_SECS="2")
        result = subprocess.run([str(ROOT / "target/debug/load")], cwd=ROOT, env=failed_environment,
                                text=True, capture_output=True, timeout=8)
        assert result.returncode == 1, "An unavailable policy must not hang the client"
        assert '"paused":true' in result.stdout
        assert "Workload deadline exceeded" in result.stderr
    print("Smoke test passed: v3 discovery, real catalog operations, permissions, and a real Retry-After delay")


if __name__ == "__main__":
    main()
