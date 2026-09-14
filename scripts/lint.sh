#!/usr/bin/env bash
# Run all linters and report a combined result.
set -uo pipefail
cd "$(dirname "$0")/.."

status=0
echo "==> ruff"
scripts/ruff.sh || status=1
echo "==> flake8"
scripts/flake8.sh || status=1
exit "$status"
