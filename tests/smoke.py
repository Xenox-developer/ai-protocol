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
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def redact_logs(text, environment):
    for key, value in environment.items():
        if value and any(word in key.upper() for word in ("TOKEN", "SECRET", "KEY", "PASSWORD")):
            text = text.replace(value, "<redacted>")
    return text


@contextmanager
def running(command, environment):
    # Keep startup diagnostics without filling a pipe or exposing credentials.
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        failed = False
        try:
            yield process
        except BaseException:
            failed = True
            raise
        finally:
            exit_before_cleanup = process.poll()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if failed:
                # Read only after the child stops writing to this shared file.
                log.seek(0)
                output = redact_logs(log.read(), environment)
                print(f"Smoke child {Path(command[0]).name}, pid={process.pid}, "
                      f"exit before cleanup={exit_before_cleanup}:\n"
                      + (output or "<no child output>"), file=sys.stderr)


def ready(process, client, url, expected, *, startup_timeout=30):
    deadline = time.monotonic() + startup_timeout
    last_result = "no probe completed"
    while (remaining := deadline - time.monotonic()) > 0:
        if (exit_code := process.poll()) is not None:
            raise RuntimeError(f"Smoke-test service exited during startup: exit={exit_code}; {last_result}")
        try:
            # A single slow probe must not consume the whole startup allowance.
            status = client.get(url, timeout=min(1, remaining)).status_code
            last_result = f"HTTP {status}, expected {expected}"
            if status == expected:
                return
        except httpx.TransportError as error:
            last_result = type(error).__name__
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"Smoke-test service did not become ready within {startup_timeout}s: {url}; last probe: {last_result}")


def main():
    with socket.socket() as gateway_probe, socket.socket() as catalog_probe:
        gateway_probe.bind(("127.0.0.1", 0))
        catalog_probe.bind(("127.0.0.1", 0))
        gateway_port = gateway_probe.getsockname()[1]
        catalog_port = catalog_probe.getsockname()[1]
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
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
