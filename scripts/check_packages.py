"""Build compact unpublished distributions and verify isolated Rust/Python consumers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(*command, cwd=ROOT, env=None):
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=300)


def inspect(names):
    forbidden = {"target", "benchmarks", "integration", ".env", ".env.demo", ".git", ".venv", "__pycache__"}
    if any(forbidden.intersection(Path(name).parts) for name in names):
        raise RuntimeError("Distribution contains development outputs or private files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory for the verified local artifacts")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("The output directory must be new")
    version = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
    python_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if version != python_version:
        raise RuntimeError("Release package versions differ")
    with tempfile.TemporaryDirectory(prefix="ai-package-check-") as directory:
        temporary = Path(directory)
        artifacts = temporary / "dist"
        run(sys.executable, "-m", "build", "--outdir", str(artifacts))
        run("cargo", "package", "--locked", "--allow-dirty", "--no-verify")
        target = (ROOT / os.environ.get("CARGO_TARGET_DIR", "target")).resolve()
        crate = target / "package" / f"ai-protocol-{version}.crate"
        shutil.copy2(crate, artifacts / crate.name)
        with tarfile.open(crate) as archive:
            names = archive.getnames()
            inspect(names)
            archive.extractall(temporary / "rust", filter="data")
        crate_root = temporary / "rust" / f"ai-protocol-{version}"
        environment = os.environ.copy()
        environment["CARGO_TARGET_DIR"] = str(target)
        run("cargo", "check", "--locked", "--all-targets", "--manifest-path", str(crate_root / "Cargo.toml"), env=environment)
        wheel, = artifacts.glob("*.whl")
        sdist, = artifacts.glob("*.tar.gz")
        with zipfile.ZipFile(wheel) as archive:
            inspect(archive.namelist())
            assert len([name for name in archive.namelist() if name.endswith(".schema.json")]) == 4
        with tarfile.open(sdist) as archive:
            inspect(archive.getnames())
            assert not any("/tests/" in name for name in archive.getnames())
        if any(path.stat().st_size > 5 * 1024 * 1024 for path in (crate, wheel, sdist)):
            raise RuntimeError("Unexpectedly large release artifact")
        venv.EnvBuilder(with_pip=True).create(temporary / "venv")
        python = temporary / "venv" / "bin" / "python"
        run(str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", str(wheel), cwd=temporary)
        run(sys.executable, str(ROOT / "scripts/check_rust_library.py"), "--crate-root", str(crate_root),
            "--python", str(python), "--minimal-sdk", cwd=temporary)
        report = {"version": version,
                  "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)),
                  "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts.iterdir()},
                  "artifacts": {path.name: path.stat().st_size for path in artifacts.iterdir()},
                  "checks": ["crate all-target check", "external Rust application", "wheel built from sdist",
                             "fresh Python installation", "installed SDK without OpenAI", "real HTTP conformance/native call"]}
        print(json.dumps(report, indent=2))
        if args.output:
            shutil.copytree(artifacts, args.output)
            (args.output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: unpublished distribution artifacts and isolated consumers.")


if __name__ == "__main__":
    main()
