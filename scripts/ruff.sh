#!/usr/bin/env bash
# Run the ruff linter. Extra arguments are passed through, e.g. `scripts/ruff.sh --fix`.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check "$@" src tests
