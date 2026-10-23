#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cargo fmt --check
cargo check --locked --all-targets
cargo build --locked --bins
cargo test --locked --all-targets
cargo test --locked --doc
cargo doc --locked --no-deps --lib
python scripts/check_docs.py
python scripts/check_operations.py
python scripts/check_rust_library.py
python -m unittest discover -s tests -v
python tests/smoke.py
python examples/quickstart.py
python tests/trial_manifest.py
trial_results="$(mktemp -d /tmp/ai-verify.XXXXXX)"
trap 'rm -rf "$trial_results"' EXIT
python examples/service_integration_demo.py --output "$trial_results/integration"
python scripts/check_packages.py
