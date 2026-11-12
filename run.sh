#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
uv sync --frozen --no-dev
exec .venv/bin/python -m aspects_overlay "$@"
