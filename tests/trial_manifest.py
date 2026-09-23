"""Execute the template and a sample exercise solution without changing any Rust source."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from protocol_client import discover, execute, service_client
from quickstart import child
from service_integration_demo import free_port, until


def main():
    env = {'PATH': os.environ['PATH'], 'PYTHONUNBUFFERED': '1'}
    ports = set()
    while len(ports) < 2:
        ports.add(free_port())
    upstream_port, gateway_port = sorted(ports)
    origin = f'http://127.0.0.1:{gateway_port}'
    manifest = json.loads((ROOT / 'services/template.json').read_text())
    full, limited = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix='manifest-trial-', dir='/tmp') as directory, ExitStack() as stack:
        upstream = stack.enter_context(child(
            [sys.executable, 'examples/support_service.py'], env | {'SUPPORT_PORT': str(upstream_port)},
        ))
        with httpx.Client(trust_env=False, timeout=1) as probe:
            until(lambda: probe.get(f'http://127.0.0.1:{upstream_port}/api/tickets/search').status_code == 200, [upstream])
        for with_exercise in (False, True):
            if with_exercise:
                manifest['operations'].append(dict(name='find_records', description='Search training records by text.',
                    path='/records/find', upstream_path='/api/tickets/search',
                    parameters={'phrase': {'type': 'string', 'upstream': 'text'}}))
                manifest['credentials'][0]['operations'].append('find_records')
            config = Path(directory) / 'service.json'
            config.write_text(json.dumps(manifest))
            gateway_env = env | {'GATEWAY_PORT': str(gateway_port), 'UPSTREAM_URL': f'http://127.0.0.1:{upstream_port}',
                                 'SERVICE_CONFIG': str(config), 'SERVICE_AGENT_TOKEN': full, 'SERVICE_READ_TOKEN': limited}
            with child([str(ROOT / 'target/debug/ai-protocol')], gateway_env) as gateway, service_client(full) as http:
                until(lambda: http.get(origin + '/agent-policy').status_code == 200, [gateway, upstream])
                operations = discover(http, base_url=origin)
                assert len(operations) == (2 if with_exercise else 1)
                assert execute(http, operations[0], {'id': 101}, base_url=origin)['ticket']['id'] == 101
                if with_exercise:
                    assert len(execute(http, operations[1], {'phrase': 'password'}, base_url=origin)['tickets']) == 2
                    denied = http.post(origin + operations[1]['path'], headers={'Authorization': f'Bearer {limited}'}, json={'phrase': 'password'})
                    assert denied.status_code == 403
                    with service_client(limited) as reader:
                        assert [op['name'] for op in discover(reader, base_url=origin)] == ['read_record']
                assert http.get(origin + '/agent-policy').json()['outstanding'] == 0
    print('PASS: unmodified template, new mapped operation, token permissions, outstanding=0; no Rust edits.')


if __name__ == '__main__':
    main()
