# Preparing a release

Package preparation is automated; publication requires explicit maintainer
authorization and registry credentials. The framework preparation task does not
publish packages, create a GitHub release or deploy services.

1. Review the diff, compatibility/migration notes, supported platforms and security
   reports. Update `Cargo.toml`, `pyproject.toml`, the changelog and documentation
   for the intended version. Follow [versioning rules](compatibility.md).
2. Use the pinned Rust toolchain and Python >=3.12. Install `requirements.txt`,
   run `bash scripts/verify.sh`, and require successful Ubuntu/macOS CI for the
   exact source commit being released.
3. Run `python scripts/check_packages.py --output dist/release-VERSION` with a new
   output directory. It builds a wheel from the sdist, checks crate contents and
   all targets, installs the wheel in a fresh environment without OpenAI, and
   runs a separate Rust application from the extracted crate with real HTTP SDK
   and conformance calls. A local verification report records artifact sizes.
4. Inspect the archive contents and metadata, license, dependency changes, and
   changelog. Retain exact artifacts/checksums and the source commit in release
   notes. Do not include `.env` files, virtual environments, raw benchmarks,
   tokens or unrelated application data. `--allow-dirty` supports review of an
   unpublished working snapshot; publish only reviewed committed sources.
5. Only after explicit authorization, use the registries' normal authenticated
   publication procedures and create a matching Git tag/release. Confirm artifact
   identities and install from the registries in another clean environment before
   advertising registry installation commands. Never commit registry credentials.

`cargo package --no-verify` is used only to avoid a redundant isolated dependency
build; the extracted crate is checked across all targets and consumed by a
separate compiled application. This is verified packaging, not evidence that a
registry accepts the package or that a public release has occurred. Historical
benchmark results are not rerun automatically and do not establish new performance
claims. The external developer trial remains deliberately skipped.
