#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — Bootstrap development environment for rastreador-convocatorias
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creating Python virtual environment..."
python3 -m venv .venv

echo "==> Activating venv and upgrading pip..."
source .venv/bin/activate
pip install --upgrade pip

echo "==> Installing project in editable mode with dev extras..."
pip install -e ".[dev]"

echo "==> Installing Playwright Chromium browser..."
playwright install chromium

echo ""
echo "✅ Setup complete. Activate with:  source .venv/bin/activate"
