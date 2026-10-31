#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
# Fewer glibc malloc arenas: Luna's worker threads otherwise each reserve their own heap,
# which raises resident memory on the Pi. Set MALLOC_ARENA_MAX yourself to change it.
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
exec $PY main.py --fullscreen "$@"
