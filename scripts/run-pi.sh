#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
exec $PY main.py --fullscreen
