"""Validate, preserve and publish one stable release without exposing credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

import httpx

REPOSITORY = "Xenox-developer/ai-protocol"
AGENT = f"AI-Protocol-release/1.0 (https://github.com/{REPOSITORY})"


def command(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True, timeout=300)


def output(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=60).strip()


def version_for(tag, cargo, python):
    if not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
        raise ValueError("Use a stable release tag vMAJOR.MINOR.PATCH")
    if (cargo["package"]["name"] != "ai-protocol" or python["project"]["name"] != "ai-protocol-client"
            or cargo["package"]["version"] != tag[1:] or python["project"]["version"] != tag[1:]):
        raise ValueError("Tag and both package names/versions must match")
    return tag[1:]


def source_info(source, tag):
    version = version_for(tag, tomllib.loads((source / "Cargo.toml").read_text()),
                          tomllib.loads((source / "pyproject.toml").read_text()))
    if output("git", "status", "--porcelain", cwd=source):
        raise ValueError("Release source must be clean")
    return version, output("git", "rev-parse", "HEAD", cwd=source)


def gh_json(*args):
    return json.loads(output("gh", *args))


def release_info(tag, commit):
    release = gh_json("release", "view", tag, "--repo", REPOSITORY, "--json",
                      "tagName,isDraft,isPrerelease,assets,body")
    if release["tagName"] != tag or release["isDraft"] or release["isPrerelease"]:
        raise ValueError("Publication requires a published, non-prerelease GitHub release")
    ref = gh_json("api", f"repos/{REPOSITORY}/git/ref/tags/{tag}")["object"]
    for _ in range(5):
        if ref["type"] != "tag":
            break
        ref = gh_json("api", f"repos/{REPOSITORY}/git/tags/{ref['sha']}")["object"]
    if ref["type"] != "commit" or ref["sha"] != commit:
        raise ValueError("The release tag no longer matches the checked source")
    return release


def names(version):
    return (f"ai-protocol-{version}.crate", f"ai_protocol_client-{version}.tar.gz",
            f"ai_protocol_client-{version}-py3-none-any.whl")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_bundle(directory, version, commit):
    expected = set(names(version))
    if {path.name for path in directory.iterdir() if path.is_file()} != expected | {"verification.json", "SHA256SUMS"} or any(not path.is_file() for path in directory.iterdir()):
        raise ValueError("Release bundle must contain only the three packages, report and checksum file")
    report = json.loads((directory / "verification.json").read_text())
    if (report.get("source_commit") != commit or report.get("version") != version
            or report.get("working_tree_dirty") is not False or set(report.get("sha256", {})) != expected):
        raise ValueError("Artifact report must match this clean source and package version")
    for name, checksum in report["sha256"].items():
        if not re.fullmatch(r"[a-f0-9]{64}", checksum) or digest(directory / name) != checksum:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    sums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        if name in sums:
            raise ValueError("Duplicate checksum entry")
        sums[name] = checksum
    if set(sums) != expected | {"verification.json"}:
        raise ValueError("SHA256SUMS must cover exactly the packages and verification report")
    if any(digest(directory / name) != checksum for name, checksum in sums.items()):
        raise ValueError("Release checksum file does not match the bundle")
    return report["sha256"]


def prepare(source, tag, built, destination):
    version, commit = source_info(source, tag)
    release = release_info(tag, commit)
    wanted = {*names(version), "verification.json", "SHA256SUMS"}
    present = wanted.intersection(asset["name"] for asset in release["assets"])
    destination.mkdir()
    if present:
        if present != wanted:
            raise ValueError("Incomplete release assets: rerun the failed publish job from its original run")
        # Preserve original bytes: rebuilding a wheel/sdist can change timestamps.
        command("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", str(destination),
                *[arg for name in sorted(wanted) for arg in ("--pattern", name)])
        print("Using the complete existing GitHub release bundle")
    else:
        for name in (*names(version), "verification.json"):
            shutil.copy2(built / name, destination / name)
        (destination / "SHA256SUMS").write_text("".join(
            f"{digest(destination / name)}  {name}\n" for name in sorted(wanted - {"SHA256SUMS"})))
    validate_bundle(destination, version, commit)
    print(f"Verified release bundle for {tag} at {commit}")


def registry_json(url):
    with httpx.Client(trust_env=False, timeout=30, headers={"User-Agent": AGENT, "Cache-Control": "no-cache"}) as client:
        response = client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def registry_state(version, checksums):
    crate, sdist, wheel = names(version)
    rust = registry_json(f"https://crates.io/api/v1/crates/ai-protocol/{version}")
    if rust is not None and (rust["version"]["num"] != version or rust["version"]["checksum"] != checksums[crate]):
        raise ValueError("crates.io already contains different bytes for this version")
    python = registry_json(f"https://pypi.org/pypi/ai-protocol-client/{version}/json")
    published = {} if python is None else {file["filename"]: file["digests"]["sha256"] for file in python["urls"]}
    if python is not None and python["info"]["version"] != version:
        raise ValueError("Unexpected PyPI version")
    if any(name not in (sdist, wheel) or checksum != checksums[name] for name, checksum in published.items()):
        raise ValueError("PyPI already contains different files for this version")
    return rust is not None, [name for name in (sdist, wheel) if name not in published]


def attach_assets(tag, directory, release):
    existing = {asset["name"] for asset in release["assets"]}
    present = existing.intersection(path.name for path in directory.iterdir())
    if present:
        with tempfile.TemporaryDirectory(prefix="release-asset-check-") as temporary:
            command("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", temporary,
                    *[arg for name in sorted(present) for arg in ("--pattern", name)])
            if any(digest(Path(temporary) / name) != digest(directory / name) for name in present):
                raise ValueError("Existing GitHub assets differ; never overwrite a release bundle")
    for path in sorted(directory.iterdir()):
        if path.name not in existing:
            command("gh", "release", "upload", tag, str(path), "--repo", REPOSITORY)


def publish(source, tag, directory):
    version, commit = source_info(source, tag)
    checksums = validate_bundle(directory, version, commit)
    release = release_info(tag, commit)
    if not os.environ.get("CARGO_REGISTRY_TOKEN") or not os.environ.get("PYPI_API_TOKEN"):
        raise ValueError("Both registry environment secrets must be configured")
    rust_exists, python_missing = registry_state(version, checksums)
    # Attach all verified files before either registry upload, enabling recovery.
    attach_assets(tag, directory, release)
    environment = os.environ.copy()
    cargo_token = environment.pop("CARGO_REGISTRY_TOKEN")
    pypi_token = environment.pop("PYPI_API_TOKEN")
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "CARGO_REGISTRIES_CRATES_IO_TOKEN", "TWINE_PASSWORD", "TWINE_REPOSITORY_URL"):
        environment.pop(key, None)
    if not rust_exists:
        command("cargo", "package", "--locked", "--no-verify", cwd=source, env=environment)
        target = Path(environment.get("CARGO_TARGET_DIR", str(source / "target")))
        if not target.is_absolute():
            target = source / target
        if digest(target / "package" / names(version)[0]) != checksums[names(version)[0]]:
            raise ValueError("Cargo would upload a different crate than the tested artifact")
        command("cargo", "publish", "--locked", "--registry", "crates-io", "--no-verify", cwd=source,
                env=environment | {"CARGO_REGISTRY_TOKEN": cargo_token})
    else:
        print("crates.io already matches; no upload needed")
    if python_missing:
        command(sys.executable, "-m", "twine", "check", "--strict", *[str(directory / name) for name in python_missing], env=environment)
        command(sys.executable, "-m", "twine", "upload", "--non-interactive", "--disable-progress-bar",
                "--repository-url", "https://upload.pypi.org/legacy/", "--username", "__token__",
                *[str(directory / name) for name in python_missing], env=environment | {"TWINE_PASSWORD": pypi_token})
    else:
        print("PyPI already matches; no upload needed")
    for attempt in range(12):
        rust_exists, python_missing = registry_state(version, checksums)
        if rust_exists and not python_missing:
            break
        if attempt == 11:
            raise ValueError("Registry visibility timed out; retry this publish job with the same artifacts")
        time.sleep(5)
    release = release_info(tag, commit)
    start, end = "<!-- ai-protocol-publication:start -->", "<!-- ai-protocol-publication:end -->"
    block = (f"{start}\n## Automated registry publication\n\n"
             f"Published and verified: [crates.io {version}](https://crates.io/crates/ai-protocol/{version}) "
             f"and [PyPI {version}](https://pypi.org/project/ai-protocol-client/{version}/).\n\n"
             f"Registry checksums match the tested release assets. Source: `{commit}`. "
             f"[Workflow run](https://github.com/{REPOSITORY}/actions/runs/{os.environ['GITHUB_RUN_ID']}).\n{end}")
    body = release["body"]
    if start in body or end in body:
        if body.count(start) != 1 or body.count(end) != 1 or body.index(start) > body.index(end):
            raise ValueError("Malformed automated publication block")
        body = body[:body.index(start)] + block + body[body.index(end) + len(end):]
    else:
        body = body.rstrip() + "\n\n" + block + "\n"
    notes = directory.parent / "publication-notes.md"
    notes.write_text(body)
    command("gh", "release", "edit", tag, "--repo", REPOSITORY, "--notes-file", str(notes))
    print(f"Published and verified both packages for {tag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["tag", "validate", "prepare", "publish"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--built", type=Path)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    if args.action == "tag":
        if not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", args.tag):
            raise ValueError("Use a stable release tag vMAJOR.MINOR.PATCH")
        return
    source = args.source.resolve()
    if args.action == "validate":
        version, commit = source_info(source, args.tag)
        release_info(args.tag, commit)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as result:
                result.write(f"version={version}\ncommit={commit}\ntag={args.tag}\n")
        print(f"Validated {args.tag} at {commit}")
    elif args.action == "prepare":
        prepare(source, args.tag, args.built.resolve(), args.artifacts.resolve())
    else:
        publish(source, args.tag, args.artifacts.resolve())


if __name__ == "__main__":
    main()
