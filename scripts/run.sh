#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run.sh — Activate venv and run the rastreador-convocatorias module
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Run setup.sh first."
    exit 1
fi

source .venv/bin/activate
python -m rastreador_convocatorias "$@"
