#!/usr/bin/env bash
# Launch the Streamlit analysis dashboard (Linux / Raspberry Pi).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Prefer python3; fall back to python.
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

if ! "${PYTHON_CMD}" -m streamlit run dashboard.py; then
    echo
    echo "Failed to start the dashboard." >&2
    echo "Run ./install_requirements.sh first to install dependencies." >&2
    exit 1
fi
