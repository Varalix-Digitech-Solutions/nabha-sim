#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "No virtualenv found at ./$VENV"
  echo "Create one with:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

exec "$VENV/bin/python" app.py
