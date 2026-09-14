#!/usr/bin/env bash
# Run the flake8 linter. Extra arguments are passed through to flake8.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run flake8 "$@" src tests
