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
import signal
import socket
import subprocess
import sys
import time
import tempfile
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


async def run_one(root, config, scenario, tasks, mode, repetition, *, clients=None,
                  owners=('demo-owner', 'other-owner'), label=None, credentials=None):
    path = root / (label or f"{scenario['name']}-r{repetition}-{mode}")
    path.mkdir()
    env = os.environ.copy()
    for name in ('OPENAI_API_KEY', 'TEST_429'):
        env.pop(name, None)
    for name in ('AGENT_TOKEN_1', 'AGENT_TOKEN_2', 'AGENT_TOKEN_3', 'AGENT_TOKEN_4', 'OTHER_AGENT_TOKEN', 'INTERACTIVE_TOKEN', 'PRODUCT_ONLY_TOKEN', 'ADMIN_TOKEN'):
        env[name] = credentials[name] if credentials is not None else secrets.token_urlsafe(32)
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
        processes = []
        dispatcher = None
        changes = None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=3) as http:
                base = f'http://127.0.0.1:{port}'
                def headers(name):
                    return {'Authorization': 'Bearer ' + env[name]}
                startup_discovery = 0
                cleanup_discovery = 0
                for _ in range(100):
                    if gateway.poll() is not None:
                        raise RuntimeError('Private gateway failed; see gateway.log')
                    try:
                        startup_discovery += 1
                        if (await http.get(base + '/agent-policy')).status_code == 401:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(.05)
                else:
                    raise RuntimeError('Private gateway startup timed out')
                roles = tuple('AGENT_TOKEN_1' if owner == 'demo-owner' else 'OTHER_AGENT_TOKEN' for owner in owners) + ('INTERACTIVE_TOKEN',)
                # Same warmup and quiescent start for every mode and repetition.
                for role in roles:
                    response = await http.post(base + '/search', headers=headers(role), json={'query': 'warmup-' + role})
                    response.raise_for_status()
                await asyncio.sleep(.1)
                if mode == 'S':
                    assert clients is not None and owners == ('demo-owner',)
                    directory = stack.enter_context(tempfile.TemporaryDirectory(prefix='ai-dispatch-', dir='/tmp'))
                    env['DISPATCH_SOCKET'] = str(Path(directory) / 'socket')
                    dispatch_env = {**env, 'DISPATCH_TOKEN_VARS': ','.join(g['token_env'] for g in clients if g['id'] != 'interactive'),
                                    'DISPATCH_CAPACITY': str(config['max_pending']), 'DISPATCH_TRACE_PATH': str(path / 'dispatcher-events.jsonl')}
                    dispatcher = subprocess.Popen([str(BIN / 'dispatcher')], cwd=ROOT, env=dispatch_env,
                                                  stdout=stack.enter_context((path / 'dispatcher.log').open('w')), stderr=subprocess.STDOUT)
                    stack.callback(stop, dispatcher)
                    for _ in range(200):
                        if dispatcher.poll() is not None:
                            raise RuntimeError('Private dispatcher startup failed')
                        if Path(env['DISPATCH_SOCKET']).exists():
                            break
                        await asyncio.sleep(.025)
                    else:
                        raise RuntimeError('Private dispatcher startup timed out')
                    save(path / 'dispatcher.json', {'pid': dispatcher.pid, 'capacity': config['max_pending'],
                                                   'token_vars': dispatch_env['DISPATCH_TOKEN_VARS'].split(','), 'transport': 'private_unix_socket'})
                epoch = time.time() + .5
                start = asyncio.get_running_loop().time() + (epoch - time.time())
                client_config = {**config, 'mode': mode, 'start_unix_s': epoch, 'tasks': tasks,
                                 'scenario': scenario['name'], 'repetition': repetition}
                save(path / 'client.json', client_config)
                groups = clients if clients is not None else [{'id': 'client', 'tasks': tasks}]
                manifest = []
                for group in groups:
                    client_env = env.copy()
                    if clients is not None:
                        # Each independent worker receives only its own service credential.
                        for key in tuple(client_env):
                            if key.endswith('_TOKEN') or key.startswith('AGENT_TOKEN_') or key == 'DISPATCH_TOKEN_VARS':
                                client_env.pop(key)
                        credential = group['token_env']
                        client_env['INTERACTIVE_TOKEN' if group['id'] == 'interactive' else 'AGENT_TOKEN_1'] = env[credential]
                    prefix = group['id']
                    save(path / f'{prefix}.json', {**client_config, 'tasks': group['tasks']})
                    client = subprocess.Popen([str(BIN / 'bench_client'), str(path / f'{prefix}.json'), str(path / f'{prefix}-events.jsonl')],
                                              cwd=ROOT, env=client_env, stdout=stack.enter_context((path / f'{prefix}.log').open('w')), stderr=subprocess.STDOUT)
                    processes.append(client)
                    stack.callback(stop, client)
                    manifest.append({'id': prefix, 'pid': client.pid, 'token_env': group.get('token_env'),
                                     'tasks': [task['id'] for task in group['tasks']]})
                if clients is not None:
                    save(path / 'processes.json', manifest)
                async def resize():
                    with (path / 'admin.jsonl').open('w') as events:
                        for change in scenario.get('changes', []):
                            await asyncio.sleep(max(0, start + change['at_s'] - asyncio.get_running_loop().time()))
                            async def update(owner):
                                requested = asyncio.get_running_loop().time() - start
                                response = await http.patch(base + f'/admin/principals/{owner}/limits',
                                                            headers=headers('ADMIN_TOKEN'), json={'max_outstanding': change['limit']})
                                response.raise_for_status()
                                events.write(json.dumps({'scheduled_s': change['at_s'], 'request_started_s': requested, 'actual_s': asyncio.get_running_loop().time() - start,
                                                         'response': response.json()}) + '\n')
                                events.flush()
                            await asyncio.gather(*(update(owner) for owner in owners))
                changes = asyncio.create_task(resize())
                returncodes = await asyncio.wait_for(asyncio.gather(*(asyncio.to_thread(client.wait) for client in processes)),
                                                      timeout=config['run_timeout_s'] + 3)
                if any(returncodes):
                    raise RuntimeError('Benchmark client failed; see client.log')
                await changes
                # The client deadline does not cancel already accepted gateway work.
                # Wait for real owner budgets to drain before stopping private instances.
                until = asyncio.get_running_loop().time() + config['cleanup_timeout_s']
                while True:
                    outstanding = 0
                    for role in roles:
                        cleanup_discovery += 1
                        response = await http.get(base + '/agent-policy', headers=headers(role))
                        response.raise_for_status()
                        outstanding += response.json()['outstanding']
                    if outstanding == 0:
                        break
                    if asyncio.get_running_loop().time() >= until:
                        raise RuntimeError(f'Cleanup failed: outstanding={outstanding}')
                    await asyncio.sleep(.05)
                await asyncio.sleep(.06)
                save(path / 'cleanup.json', {'outstanding': outstanding, 'unix_s': time.time(),
                                             'harness_discovery': {'startup': startup_discovery, 'cleanup': cleanup_discovery}})
        finally:
            if changes and not changes.done():
                changes.cancel()
                await asyncio.gather(changes, return_exceptions=True)
            for client in processes:
                stop(client)
            if dispatcher and dispatcher.poll() is None:
                dispatcher.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(asyncio.to_thread(dispatcher.wait), timeout=7)
                except asyncio.TimeoutError:
                    stop(dispatcher)
                    raise RuntimeError('Dispatcher did not drain on shutdown')
            stop(gateway)
            server.close()
            await server.wait_closed()
            await upstream.close()
    if clients is not None:
        merged = []
        for group in groups:
            with (path / f"{group['id']}-events.jsonl").open() as stream:
                merged.extend({**json.loads(line), 'client_id': group['id']} for line in stream)
        if mode == 'S':
            assignment = {task['id']: group['id'] for group in groups for task in group['tasks']}
            with (path / 'dispatcher-events.jsonl').open() as stream:
                for line in stream:
                    event = json.loads(line)
                    event.update(at_ms=(event['unix_s'] - epoch) * 1000,
                                 client_id=assignment.get(event.get('id'), 'dispatcher'), executor='dispatcher')
                    merged.append(event)
        merged.sort(key=lambda event: event['at_ms'])
        with (path / 'client-events.jsonl').open('w') as stream:
            for event in merged:
                stream.write(json.dumps(event) + '\n')
    print(f"Completed {path.name}", flush=True)


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
