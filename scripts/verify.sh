#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cargo fmt --check
cargo check --locked --all-targets
cargo build --locked --bins
cargo test --locked --all-targets
python -m unittest discover -s tests -v
python tests/smoke.py
python examples/quickstart.py
python tests/trial_manifest.py
trial_results="$(mktemp -d /tmp/ai-verify.XXXXXX)"
trap 'rm -rf "$trial_results"' EXIT
python examples/service_integration_demo.py --output "$trial_results/integration"
