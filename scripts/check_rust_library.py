"""Build and run a separate application using only the public Rust interface."""
import argparse
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


SDK_CHECK = """
import asyncio, importlib.util, os
from pathlib import Path
import ai_protocol
from ai_protocol import AsyncClient
from ai_protocol.conformance import check_service
origin, token = os.environ['SERVICE_URL'], os.environ['AGENT_TOKEN_1']
if os.environ['CHECK_MINIMAL_SDK'] == '1':
    assert importlib.util.find_spec('openai') is None
    assert not Path(ai_protocol.__file__).resolve().is_relative_to(Path(os.environ['CHECK_SOURCE_ROOT']))
assert check_service(origin, token, operation='find_documents', params={'query': 'rust'})['version'] == 3
async def run():
    async with AsyncClient(origin, token) as client:
        assert client.policy['service_id'] == 'documents'
        assert await client.call('find_documents', {'query': 'rust'}) == {'documents': ['Rust introduction']}
        assert client.active_attempts == 0
asyncio.run(run())
print('PASS: installed SDK, full discovery, native call and conformance outside the checkout.')
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crate-root", type=Path, default=ROOT)
    parser.add_argument("--python", default=sys.executable, help="Interpreter with the installed SDK")
    parser.add_argument("--minimal-sdk", action="store_true")
    args = parser.parse_args()
    crate_root = args.crate_root.resolve()
    dependencies = tomllib.loads((crate_root / "Cargo.toml").read_text())["dependencies"]
    with tempfile.TemporaryDirectory(prefix="ai-rust-consumer-") as directory:
        project = Path(directory)
        (project / "src").mkdir()
        shutil.copy2(crate_root / "examples/native_service.rs", project / "src/main.rs")
        shutil.copy2(crate_root / "Cargo.lock", project / "Cargo.lock")
        manifest = [
            '[package]', 'name = "ai-protocol-library-consumer"', 'version = "0.0.0"',
            'edition = "2024"', '[dependencies]',
            f'ai-protocol = {{ path = {json.dumps(str(crate_root))} }}',
        ]
        for name in ("axum", "serde", "serde_json", "tokio"):
            spec = dependencies[name]
            value = json.dumps(spec) if isinstance(spec, str) else "{ " + ", ".join(
                f"{key} = {json.dumps(value)}" for key, value in spec.items()
            ) + " }"
            manifest.append(f"{name} = {value}")
        (project / "Cargo.toml").write_text("\n".join(manifest) + "\n")
        environment = os.environ.copy()
        target = (ROOT / environment.get("CARGO_TARGET_DIR", "target")).resolve()
        environment["CARGO_TARGET_DIR"] = str(target)
        subprocess.run(
            ["cargo", "build", "--offline", "--manifest-path", str(project / "Cargo.toml")],
            cwd=ROOT, env=environment, check=True, timeout=180,
        )
        token = secrets.token_urlsafe(24)
        environment.update(NATIVE_AGENT_TOKEN=token, NATIVE_LISTEN_ADDR="127.0.0.1:0")
        process = subprocess.Popen(
            [str(target / "debug/ai-protocol-library-consumer")],
            cwd=project, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            if not select.select([process.stdout], [], [], 30)[0]:
                raise RuntimeError("The external Rust application did not become ready")
            line = process.stdout.readline().strip()
            prefix = "Native document service listening on "
            if not line.startswith(prefix):
                raise RuntimeError("The external Rust application failed during startup")
            origin = "http://" + line.removeprefix(prefix)
            http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(path, body=None):
                headers = {"Authorization": f"Bearer {token}"}
                if body is not None:
                    headers["Content-Type"] = "application/json"
                data = None if body is None else json.dumps(body).encode()
                with http.open(urllib.request.Request(origin + path, data=data, headers=headers), timeout=5) as response:
                    return json.load(response)

            policy = request("/agent-policy")
            assert policy["service_id"] == "documents"
            assert policy["version"] == 3
            assert policy["operations"][0]["name"] == "find_documents"
            assert request("/documents/find", {"query": "rust"}) == {"documents": ["Rust introduction"]}
            assert request("/agent-policy")["outstanding"] == 0
            environment.update(SERVICE_URL=origin, AGENT_TOKEN_1=token,
                               CHECK_MINIMAL_SDK=str(int(args.minimal_sdk)), CHECK_SOURCE_ROOT=str(ROOT))
            subprocess.run([args.python, "-I", "-c", SDK_CHECK], cwd=project, env=environment, check=True, timeout=30)
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
    print("PASS: external Rust application, native discovery/call, zero outstanding, graceful shutdown.")


if __name__ == "__main__":
    main()
