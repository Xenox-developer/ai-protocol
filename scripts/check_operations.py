"""Verify standalone configuration diagnostics, SIGTERM drain and grace expiry."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import select
import signal
import subprocess
import tempfile
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    token = secrets.token_urlsafe(24)
    started = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            started.set()
            release.wait(5)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    upstream.daemon_threads = True
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="ai-operations-") as directory:
            manifest = Path(directory) / "service.json"
            value = {"format_version": 1, "service_id": "operations_check",
                     "operations": [{"name": "read", "description": "Read a fixture", "path": "/records", "upstream_path": "/records",
                                     "parameters": {"query": {"type": "string", "upstream": "q"}}}],
                     "credentials": [{"token_env": "READER_TOKEN", "required": True, "principal_id": "reader", "client_class": "agent", "operations": ["read"], "max_outstanding": 2}]}
            environment = {"PATH": os.environ["PATH"], "READER_TOKEN": token, "SERVICE_CONFIG": str(manifest),
                           "UPSTREAM_URL": f"http://127.0.0.1:{upstream.server_port}", "GATEWAY_LISTEN_ADDR": "127.0.0.1:0", "GATEWAY_LOG_REQUESTS": "true"}
            value["format_version"] = 2
            manifest.write_text(json.dumps(value))
            failed = subprocess.run([str(ROOT / "target/debug/ai-protocol")], env=environment, capture_output=True, text=True, timeout=10)
            assert failed.returncode == 1 and "format_version" in failed.stderr
            assert token not in failed.stderr
            value["format_version"] = 1
            manifest.write_text(json.dumps(value))
            for grace, graceful in [(3000, True), (30, False)]:
                started.clear()
                release.clear()
                process = subprocess.Popen([str(ROOT / "target/debug/ai-protocol")], env=environment | {"GATEWAY_SHUTDOWN_GRACE_MS": str(grace)},
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    assert select.select([process.stdout], [], [], 10)[0], "Gateway startup timed out"
                    line = process.stdout.readline()
                    port = int(line.split("port ")[1].split()[0])
                    with httpx.Client(trust_env=False, timeout=5) as http, ThreadPoolExecutor(max_workers=1) as pool:
                        result = pool.submit(http.post, f"http://127.0.0.1:{port}/records", headers={"Authorization": f"Bearer {token}"}, json={"query": "private-request-input"})
                        assert started.wait(5), "Upstream execution did not start"
                        before = time.monotonic()
                        process.send_signal(signal.SIGTERM)
                        if graceful:
                            release.set()
                            assert result.result(timeout=5).json() == {"ok": True}
                            assert process.wait(timeout=5) == 0
                        else:
                            assert process.wait(timeout=3) == 1
                            assert time.monotonic() - before < 3
                            try:
                                result.result(timeout=5)
                            except httpx.TransportError:
                                pass
                    output = process.stderr.read()
                    assert token not in output and "private-request-input" not in output
                    for line in output.splitlines():
                        assert isinstance(json.loads(line), dict)
                finally:
                    release.set()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    process.stdout.close()
                    process.stderr.close()
    finally:
        release.set()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
    print("PASS: actionable safe configuration error, SIGTERM drain, bounded grace expiry and JSON logs.")


if __name__ == "__main__":
    main()
