"""First local call and three real agent processes; only owned children are stopped."""
import argparse
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time

import httpx

from protocol_client import discover, execute, service_client
from service_integration_demo import free_port, until
from setup_demo import TOKENS

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def child(command, environment, signal_on_exit=signal.SIGTERM):
    process = subprocess.Popen(command, cwd=ROOT, env=environment)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal_on_exit)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(keep_running=False):
    ports = set()
    while len(ports) < 2:
        ports.add(free_port())
    catalog_port, gateway_port = sorted(ports)
    origin = f'http://127.0.0.1:{gateway_port}'
    # Do not inherit a developer's service settings, credentials or proxy settings.
    base_env = {'PATH': os.environ['PATH'], 'PYTHONUNBUFFERED': '1'}
    tokens = {name: secrets.token_urlsafe(32) for name in TOKENS}
    with tempfile.TemporaryDirectory(prefix='ai-trial-', dir='/tmp') as directory, ExitStack() as stack:
        socket_path = str(Path(directory) / 'dispatcher.sock')
        processes = []
        print('1. Starting the catalog and gateway on free loopback ports.', flush=True)
        processes.append(stack.enter_context(child(
            [sys.executable, 'examples/catalog_service.py'],
            base_env | {'CATALOG_PORT': str(catalog_port)},
        )))
        processes.append(stack.enter_context(child(
            [str(ROOT / 'target/debug/ai-protocol')],
            base_env | tokens | {'CATALOG_PORT': str(catalog_port), 'GATEWAY_PORT': str(gateway_port)},
        )))
        with service_client(tokens['AGENT_TOKEN_1']) as http:
            until(lambda: http.get(origin + '/agent-policy').status_code == 200, processes)
            with httpx.Client(trust_env=False, timeout=1) as probe:
                until(lambda: probe.get(f'http://127.0.0.1:{catalog_port}/products/search').status_code == 200, processes)
            print('2. GET /agent-policy (no token values in this output):', flush=True)
            print(json.dumps(http.get(origin + '/agent-policy').json(), indent=2))
            operations = discover(http, base_url=origin)
            operation = next(op for op in operations if op['name'] == 'get_product')
            result = execute(http, operation, {'id': 2}, base_url=origin)
            assert result['product']['name'] == 'Brown boots'
            print('3. First successful direct call:', json.dumps(result), flush=True)
        processes.append(stack.enter_context(child(
            [str(ROOT / 'target/debug/dispatcher')],
            base_env | tokens | {'SERVICE_URL': origin, 'DISPATCH_SOCKET': socket_path,
                                 'DISPATCH_TOKEN_VARS': 'AGENT_TOKEN_1,AGENT_TOKEN_2,PRODUCT_ONLY_TOKEN'},
            signal_on_exit=signal.SIGINT,
        )))
        until(lambda: Path(socket_path).exists(), processes)
        print('4. Starting three independent client processes through one dispatcher.', flush=True)
        clients = []
        for name in ('AGENT_TOKEN_1', 'AGENT_TOKEN_2', 'PRODUCT_ONLY_TOKEN'):
            env = base_env | {'AGENT_TOKEN_1': tokens[name], 'DISPATCH_SOCKET': socket_path}
            clients.append(stack.enter_context(child(
                [sys.executable, 'examples/dispatch_client.py', operation['name'], '{"id":2}'], env,
            )))
        for client in clients:
            assert client.wait(timeout=15) == 0, 'A dispatched client failed'
        with service_client(tokens['AGENT_TOKEN_1']) as http:
            assert http.get(origin + '/agent-policy').json()['outstanding'] == 0
        print('PASS: first call, three dispatched clients, final outstanding=0.', flush=True)
        if keep_running:
            # This private file exposes only one agent credential, never all owner roles.
            agent_file = Path(directory) / 'agent.env'
            with os.fdopen(os.open(agent_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
                for key, value in {'AGENT_TOKEN_1': tokens['AGENT_TOKEN_1'], 'SERVICE_URL': origin,
                                   'DISPATCH_SOCKET': socket_path}.items():
                    output.write(f"export {key}='{value}'\n")
            print(f'Optional agent terminal: source {agent_file}', flush=True)
            print('Keep this terminal open. Press Ctrl+C here to stop only these children.', flush=True)
            while True:
                time.sleep(1)
    assert not Path(socket_path).exists()
    assert all(process.poll() is not None for process in processes + clients)
    print('5. Owned processes stopped; temporary credentials and socket removed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep-running', action='store_true', help='Keep the private demo running until Ctrl+C')
    args = parser.parse_args()
    try:
        main(args.keep_running)
    except KeyboardInterrupt:
        print('Owned processes stopped; temporary credentials and socket removed.')
