"""Verify the intended repository snapshot in a fresh directory and environment.

Includes tracked and nonignored untracked files so no commit is required.
No project binaries, local secrets, virtualenv, .git or parent files are copied.
Installed compilers/Python are tools, not reused build outputs; Cargo downloads
and Python packages are installed into fresh temporary locations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main(report_path):
    paths = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT,
    ).decode().split('\0')
    paths = sorted(set(path for path in paths if path and (ROOT / path).is_file()))
    source_hashes = {}
    rustup_home = subprocess.check_output(['rustup', 'show', 'home'], text=True).strip()
    # A short /tmp path also keeps Unix socket names inside the OS length limit.
    with tempfile.TemporaryDirectory(prefix='ai-clean-', dir='/tmp') as directory:
        base = Path(directory)
        project = base / 'checkout'
        project.mkdir()
        for relative in paths:
            path = Path(relative)
            if any(part in ('.git', 'target', '.venv', 'venv', '__pycache__') for part in path.parts) or path.name.startswith('.env') and path.name != '.env.example':
                raise RuntimeError('A local-only file is included in the intended snapshot')
            source = ROOT / path
            if source.is_symlink():
                raise RuntimeError('Review symlinks explicitly before exporting a clean snapshot')
            target = project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            source_hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
        environment = {'PATH': os.environ['PATH'], 'HOME': str(base / 'home'),
                       'CARGO_HOME': str(base / 'cargo'), 'RUSTUP_HOME': rustup_home,
                       'PYTHONNOUSERSITE': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
        Path(environment['HOME']).mkdir()
        commands = [
            [sys.executable, '-m', 'venv', str(project / '.venv')],
            [str(project / '.venv/bin/python'), '-m', 'pip', 'install', '--no-cache-dir', '-r', 'requirements.txt'],
            ['bash', 'scripts/verify.sh'],
        ]
        versions = {name: subprocess.check_output(command, text=True).strip() for name, command in {
            'rustc': ['rustc', '--version'], 'cargo': ['cargo', '--version'], 'python': [sys.executable, '--version'],
        }.items()}
        for command in commands:
            print('Running clean step:', Path(command[0]).name, ' '.join(command[1:3]), flush=True)
            subprocess.run(command, cwd=project, env=environment, check=True)
            environment['PATH'] = str(project / '.venv/bin') + os.pathsep + os.environ['PATH']
        result = {'passed': True, 'platform': sys.platform, 'versions': versions,
                  'copied_files': len(paths), 'input_source_sha256': source_hashes,
                  'python_packages': subprocess.check_output([str(project / '.venv/bin/python'), '-m', 'pip', 'freeze'], cwd=project, env=environment, text=True).splitlines(),
                  'fresh_cargo_home': True, 'fresh_python_venv': True, 'fresh_build': True,
                  'copied_git_directory': False, 'inherited_project_environment': False,
                  'steps': ['create venv', 'pip install --no-cache-dir -r requirements.txt', 'bash scripts/verify.sh'],
                  'note': 'Includes intended uncommitted in-repository files; this is not validation of a published commit. Installed compiler toolchains are reused, not project build outputs.'}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open('x') as output:
            output.write(json.dumps(result, indent=2) + '\n')
    print('PASS: clean snapshot verified; temporary checkout, tokens and build outputs removed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True, help='New JSON report path, outside the temporary checkout')
    args = parser.parse_args()
    main(args.report.resolve())
