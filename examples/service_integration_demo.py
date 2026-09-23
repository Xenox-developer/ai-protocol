"""Targeted real-process integration check, temporary credentials, no LLM or external API."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx

from dispatch_client import DispatcherClient
from protocol_client import discover, execute, service_client

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def until(check, processes):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError('An owned demo process exited; inspect output logs')
        try:
            if check():
                return
        except (httpx.TransportError, OSError):
            pass
        time.sleep(.03)
    raise TimeoutError('Demo startup or cleanup deadline exceeded')


async def run(output):
    processes, logs = [], []
    base_env = {'PATH': os.environ['PATH'], 'PYTHONUNBUFFERED': '1'}
    ports = set()
    while len(ports) < 4:
        ports.add(free_port())
    catalog_port, support_port, catalog_gateway, support_gateway = sorted(ports)
    # Distinct per-role credentials, reused across services on purpose: the origin must isolate gates.
    full, second, limited, interactive, admin = [secrets.token_urlsafe(24) for _ in range(5)]
    origins = {'catalog': f'http://127.0.0.1:{catalog_gateway}', 'support': f'http://127.0.0.1:{support_gateway}'}
    def launch(name, command, env):
        log = (output / f'{name}.log').open('w')
        logs.append(log)
        p = subprocess.Popen(command, cwd=ROOT, env=base_env | env, stdout=log, stderr=log)
        processes.append(p)
        return p
    def rows(path):
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    with tempfile.TemporaryDirectory(prefix='services-demo-') as private:
        sockets = {name: str(Path(private) / f'{name}.sock') for name in origins}
        try:
            launch('catalog-upstream', [sys.executable, 'examples/catalog_service.py'], {'CATALOG_PORT': str(catalog_port)})
            launch('support-upstream', [sys.executable, 'examples/support_service.py'], {'SUPPORT_PORT': str(support_port), 'SUPPORT_TRACE_PATH': str(output / 'support-upstream.jsonl')})
            with httpx.Client(trust_env=False, timeout=1) as probe:
                until(lambda: probe.get(f'http://127.0.0.1:{catalog_port}/products/search?query=').status_code == 200 and probe.get(f'http://127.0.0.1:{support_port}/api/tickets/search?text=').status_code == 200, processes)
            environments = {
                'catalog': dict(GATEWAY_PORT=str(catalog_gateway), CATALOG_PORT=str(catalog_port), AGENT_TOKEN_1=full, AGENT_TOKEN_2=second, PRODUCT_ONLY_TOKEN=limited, INTERACTIVE_TOKEN=interactive),
                'support': dict(GATEWAY_PORT=str(support_gateway), UPSTREAM_URL=f'http://127.0.0.1:{support_port}', SERVICE_CONFIG=str(ROOT / 'services/support.json'), SUPPORT_AGENT_TOKEN=full, SUPPORT_READ_TOKEN=limited, SUPPORT_INTERACTIVE_TOKEN=interactive),
            }
            for name, env in environments.items():
                launch(f'{name}-gateway', [str(ROOT / 'target/debug/ai-protocol')], env | {'ADMIN_TOKEN': admin, 'BENCHMARK_TRACE_PATH': str(output / f'{name}-gateway.jsonl')})
            with service_client(full) as http:
                for origin in origins.values():
                    until(lambda: http.get(origin + '/agent-policy').status_code == 200, processes)
                policies = {name: http.get(origin + '/agent-policy').json() for name, origin in origins.items()}
                assert policies['catalog']['principal_id'] == policies['support']['principal_id'] == 'demo-owner'
                assert all(p['version'] == 3 and p['limits']['scope'] == 'principal' for p in policies.values())
                assert policies['catalog']['service_id'] != policies['support']['service_id']
                operations = {name: discover(http, base_url=origin) for name, origin in origins.items()}
                (output / 'discovery.json').write_text(json.dumps(policies, indent=2) + '\n')
                # Select from descriptions/schemas; this flow has no operation-name branches.
                selected = {name: next(op for op in ops if op['input_schema']['properties'].get('query', {}).get('type') == 'string') for name, ops in operations.items()}
                direct = {name: execute(http, selected[name], {'query': ''}, base_url=origin) for name, origin in origins.items()}
                assert direct['catalog']['products'] and direct['support']['tickets']
                for name, origin in origins.items():
                    response = http.patch(origin + '/admin/principals/demo-owner/limits', headers={'Authorization': f'Bearer {admin}'}, json={'max_outstanding': 1 if name == 'catalog' else 2})
                    response.raise_for_status()
            for name, origin in origins.items():
                launch(f'{name}-dispatcher', [str(ROOT / 'target/debug/dispatcher')], {'SERVICE_URL': origin, 'DISPATCH_SOCKET': sockets[name], 'DISPATCH_TOKEN_VARS': 'FULL_TOKEN,LIMITED_TOKEN', 'FULL_TOKEN': full, 'LIMITED_TOKEN': limited, 'DISPATCH_TRACE_PATH': str(output / f'{name}-dispatcher.jsonl')})
            until(lambda: all(Path(path).exists() for path in sockets.values()), processes)
            results = {}
            for name in origins:
                client = DispatcherClient(sockets[name], full)
                results[name] = await client.call(selected[name], {'query': ''}, timeout_s=10)
                assert results[name]['ok'] and results[name]['body'] == direct[name]
            # A restricted credential discovers only its allowed operation and cannot borrow the full token.
            with service_client(limited) as http:
                limited_ops = discover(http, base_url=origins['support'])
                assert len(limited_ops) == 1 and limited_ops[0]['name'] == 'get_ticket'
            restricted = DispatcherClient(sockets['support'], limited)
            before = len(rows(output / 'support-upstream.jsonl'))
            forbidden = await restricted.call(selected['support'], {'query': 'must-not-run'})
            assert forbidden['status'] == 403 and forbidden['attempts'] == 1
            assert len(rows(output / 'support-upstream.jsonl')) == before
            allowed = await restricted.call(limited_ops[0], {'id': 101})
            assert allowed['ok'] and allowed['body']['ticket']['id'] == 101
            # No caller-supplied URL or route can become a gateway/upstream destination.
            invalid = await restricted.call('unpublished_operation', {})
            assert invalid['code'] == 'invalid_dispatch_request' and invalid['attempts'] == 0
            with service_client(full) as http:
                response = http.post(origins['support'] + selected['support']['path'], json={'query': '', 'url': 'http://127.0.0.1:1'})
                assert response.status_code == 422
                changed = http.patch(origins['catalog'] + '/admin/principals/demo-owner/limits', headers={'Authorization': f'Bearer {admin}'}, json={'max_outstanding': 3})
                changed.raise_for_status()
                until(lambda: any(r.get('event') == 'policy' and r.get('limit') == 3 for r in rows(output / 'catalog-dispatcher.jsonl')), processes)
                assert http.get(origins['support'] + '/agent-policy').json()['limits']['max_outstanding'] == 2
                assert any(r.get('event') == 'policy' and r.get('limit') == 2 for r in rows(output / 'support-dispatcher.jsonl'))
            # Both independent service gates make progress with equal owner IDs and credential values.
            concurrent = await asyncio.gather(*(DispatcherClient(sockets[name], full).call(selected[name], {'query': ''}) for name in origins for _ in range(6)))
            assert all(r['ok'] for r in concurrent)
            final = {}
            with service_client(full) as http:
                for name, origin in origins.items():
                    until(lambda: http.get(origin + '/agent-policy').json()['outstanding'] == 0, processes)
                    final[name] = http.get(origin + '/agent-policy').json()
            for name in origins:
                admissions = [r for r in rows(output / f'{name}-gateway.jsonl') if r['event'] == 'admission']
                assert admissions and all(r['outstanding'] <= r['limit'] for r in admissions)
                attempts = [r for r in rows(output / f'{name}-dispatcher.jsonl') if r['event'] == 'attempt_start']
                assert all(r['local_active'] <= r['local_limit'] for r in attempts)
            assert all(not r['authorization_present'] for r in rows(output / 'support-upstream.jsonl'))
            result = dict(passed=True, service_ids=list(origins), owner='demo-owner', direct=direct, dispatched=results,
                          restricted_status=forbidden['status'], forbidden_upstream_calls=0, allowed_ticket=allowed['body'],
                          concurrent_success=len(concurrent), final_limits={n: p['limits']['max_outstanding'] for n,p in final.items()},
                          final_outstanding={n:p['outstanding'] for n,p in final.items()}, admission_violations=0, credentials_forwarded_to_upstream=False)
            (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        finally:
            # Only processes created by this invocation are signalled.
            for p in reversed(processes):
                if p.poll() is None:
                    p.send_signal(signal.SIGINT)
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait()
            for log in logs:
                log.close()
    for name in origins:
        trace = rows(output / f'{name}-dispatcher.jsonl')
        assert any(r['event'] == 'gate_final' and r['active'] == 0 for r in trace)
    print(json.dumps({'passed': True, 'output': str(output), 'final_outstanding': result['final_outstanding']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='New directory for credential-free results')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    asyncio.run(run(args.output.resolve()))
