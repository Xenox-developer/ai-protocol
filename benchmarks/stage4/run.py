"""Sequential, isolated A/B/C experiment. No LLM, paid APIs, or user servers."""
import argparse
import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

import httpx
from analyze import build_summary

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / 'target/release'


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def metadata(config):
    def optional(*args):
        try:
            return command(*args)
        except (OSError, subprocess.CalledProcessError):
            return 'unavailable'
    sources = [ROOT / 'Cargo.toml', ROOT / 'Cargo.lock', *ROOT.glob('src/**/*.rs'),
               *ROOT.glob('benchmarks/stage4/*.py'), ROOT / 'benchmarks/stage4/config.json']
    return {
        'config': config, 'commit': command('git', 'rev-parse', 'HEAD'),
        'git_status': command('git', 'status', '--short'), 'dirty': bool(command('git', 'status', '--porcelain')),
        'rustc': command('rustc', '--version'), 'cargo': command('cargo', '--version'),
        'python': sys.version, 'httpx': httpx.__version__, 'os': platform.system(),
        'os_release': platform.release(), 'architecture': platform.machine(),
        'logical_cpus': os.cpu_count(), 'machine_model': optional('sysctl', '-n', 'hw.model'),
        'cpu': optional('sysctl', '-n', 'machdep.cpu.brand_string'),
        'memory_bytes': optional('sysctl', '-n', 'hw.memsize'),
        'tokio_worker_threads_per_process': 4, 'build_profile': 'release',
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        'binary_sha256': {name: hashlib.sha256((BIN / name).read_bytes()).hexdigest() for name in ('ai-protocol', 'bench_client')},
        'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def schedule(scenario, seed):
    rng = random.Random(seed)
    tasks = []
    for kind in ('interactive', 'agent'):
        rate = scenario[kind + '_rps']
        for index in range(round(rate * scenario['duration_s'])):
            tasks.append({'class': kind, 'owner': 'other-owner' if kind == 'agent' and index % 2 else 'demo-owner',
                          'arrival_ms': 1000 * index / rate + rng.random() * min(1, 100 / rate)})
    tasks.sort(key=lambda row: row['arrival_ms'])
    for index, task in enumerate(tasks):
        task.update(id=f't{index:05}', operation='product' if rng.random() < .3 else 'search')
        task['params'] = {'id': 1000 + index} if task['operation'] == 'product' else {'query': 'task-' + task['id']}
    return tasks


class Upstream:
    """Fixed-delay asynchronous I/O model, not CPU/database simulation."""
    def __init__(self, delay_ms, stream):
        self.delay = delay_ms / 1000
        self.stream = stream
        self.handlers = set()

    def emit(self, event, key):
        self.stream.write(json.dumps({'event': event, 'key': key, 'unix_s': time.time()}) + '\n')
        self.stream.flush()

    async def handle(self, reader, writer):
        current = asyncio.current_task()
        self.handlers.add(current)
        try:
            while True:
                head = await reader.readuntil(b'\r\n\r\n')
                target = head.split(b' ', 2)[1].decode('ascii')
                parsed = urlparse(target)
                params = parse_qs(parsed.query)
                key = params.get('query', params.get('id', ['unknown']))[0]
                self.emit('start', key)
                await asyncio.sleep(self.delay)
                payload = {'products': []} if parsed.path == '/products/search' else {'product': {'id': int(key)}}
                body = json.dumps(payload).encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ' +
                             str(len(body)).encode() + b'\r\n\r\n' + body)
                await writer.drain()
                self.emit('end', key)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.handlers.discard(current)
            writer.close()
            await writer.wait_closed()

    async def close(self):
        handlers = list(self.handlers)
        for handler in handlers:
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def run_one(root, config, scenario, tasks, mode, repetition):
    path = root / f"{scenario['name']}-r{repetition}-{mode}"
    path.mkdir()
    env = os.environ.copy()
    for name in ('OPENAI_API_KEY', 'TEST_429'):
        env.pop(name, None)
    for name in ('AGENT_TOKEN_1', 'AGENT_TOKEN_2', 'OTHER_AGENT_TOKEN', 'INTERACTIVE_TOKEN', 'PRODUCT_ONLY_TOKEN', 'ADMIN_TOKEN'):
        env[name] = secrets.token_urlsafe(32)
    with ExitStack() as stack:
        upstream = Upstream(config['upstream_delay_ms'], stack.enter_context((path / 'upstream.jsonl').open('w')))
        server = await asyncio.start_server(upstream.handle, '127.0.0.1', 0)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env.update(GATEWAY_PORT=str(port), CATALOG_PORT=str(server.sockets[0].getsockname()[1]),
                   SCHEDULER_MODE='fifo' if mode == 'A' else 'weighted',
                   INTERACTIVE_MAX_WAIT_MS=str(config['interactive_max_wait_ms']), AGENT_MAX_WAIT_MS=str(config['agent_max_wait_ms']),
                   BENCHMARK_TRACE_PATH=str(path / 'server.jsonl'), TOKIO_WORKER_THREADS='4')
        gateway = subprocess.Popen([str(BIN / 'ai-protocol')], cwd=ROOT, env=env,
                                   stdout=stack.enter_context((path / 'gateway.log').open('w')), stderr=subprocess.STDOUT)
        stack.callback(stop, gateway)
        client = None
        changes = None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=3) as http:
                base = f'http://127.0.0.1:{port}'
                def headers(name):
                    return {'Authorization': 'Bearer ' + env[name]}
                for _ in range(100):
                    if gateway.poll() is not None:
                        raise RuntimeError('Private gateway failed; see gateway.log')
                    try:
                        if (await http.get(base + '/agent-policy')).status_code == 401:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(.05)
                else:
                    raise RuntimeError('Private gateway startup timed out')
                # Same warmup and quiescent start for every mode and repetition.
                for role in ('AGENT_TOKEN_1', 'OTHER_AGENT_TOKEN', 'INTERACTIVE_TOKEN'):
                    response = await http.post(base + '/search', headers=headers(role), json={'query': 'warmup-' + role})
                    response.raise_for_status()
                await asyncio.sleep(.1)
                epoch = time.time() + .5
                start = asyncio.get_running_loop().time() + (epoch - time.time())
                client_config = {**config, 'mode': mode, 'start_unix_s': epoch, 'tasks': tasks,
                                 'scenario': scenario['name'], 'repetition': repetition}
                save(path / 'client.json', client_config)
                client = subprocess.Popen([str(BIN / 'bench_client'), str(path / 'client.json'), str(path / 'client-events.jsonl')],
                                          cwd=ROOT, env=env, stdout=stack.enter_context((path / 'client.log').open('w')), stderr=subprocess.STDOUT)
                stack.callback(stop, client)
                async def resize():
                    with (path / 'admin.jsonl').open('w') as events:
                        for change in scenario.get('changes', []):
                            await asyncio.sleep(max(0, start + change['at_s'] - asyncio.get_running_loop().time()))
                            async def update(owner):
                                response = await http.patch(base + f'/admin/principals/{owner}/limits',
                                                            headers=headers('ADMIN_TOKEN'), json={'max_outstanding': change['limit']})
                                response.raise_for_status()
                                events.write(json.dumps({'scheduled_s': change['at_s'], 'actual_s': asyncio.get_running_loop().time() - start,
                                                         'response': response.json()}) + '\n')
                                events.flush()
                            await asyncio.gather(*(update(owner) for owner in ('demo-owner', 'other-owner')))
                changes = asyncio.create_task(resize())
                returncode = await asyncio.wait_for(asyncio.to_thread(client.wait), timeout=config['run_timeout_s'] + 3)
                if returncode:
                    raise RuntimeError('Benchmark client failed; see client.log')
                await changes
                # The client deadline does not cancel already accepted gateway work.
                # Wait for real owner budgets to drain before stopping private instances.
                until = asyncio.get_running_loop().time() + config['cleanup_timeout_s']
                while True:
                    outstanding = 0
                    for role in ('AGENT_TOKEN_1', 'OTHER_AGENT_TOKEN', 'INTERACTIVE_TOKEN'):
                        response = await http.get(base + '/agent-policy', headers=headers(role))
                        response.raise_for_status()
                        outstanding += response.json()['outstanding']
                    if outstanding == 0:
                        break
                    if asyncio.get_running_loop().time() >= until:
                        raise RuntimeError(f'Cleanup failed: outstanding={outstanding}')
                    await asyncio.sleep(.05)
                await asyncio.sleep(.06)
                save(path / 'cleanup.json', {'outstanding': outstanding, 'unix_s': time.time()})
        finally:
            if changes and not changes.done():
                changes.cancel()
                await asyncio.gather(changes, return_exceptions=True)
            if client:
                stop(client)
            stop(gateway)
            server.close()
            await server.wait_closed()
            await upstream.close()
    print(f"Completed {scenario['name']} repetition={repetition} mode={mode}", flush=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pilot', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.pilot:
        config.update(repetitions=1, upstream_delay_ms=1, scenarios=[{'name': 'pilot', 'duration_s': 2, 'interactive_rps': 40, 'agent_rps': 80}])
    assert config['repetitions'] >= (1 if args.pilot else 3)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    save(root / 'config.json', config)
    save(root / 'environment.json', metadata(config))
    for scenario in config['scenarios']:
        tasks = schedule(scenario, config['seed'])
        save(root / (scenario['name'] + '-schedule.json'), tasks)
        for repetition in range(1, config['repetitions'] + 1):
            order = ('ABC', 'BCA', 'CAB')[(repetition - 1) % 3]
            for mode in order:
                await run_one(root, config, scenario, tasks, mode, repetition)
    runs = build_summary(root)
    print(json.dumps({'runs': len(runs), 'admission_violations': sum(r['admission_violations'] for r in runs),
                      'final_outstanding': sum(r['final_outstanding'] for r in runs)}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
