#!/usr/bin/env bash
# Launch the Streamlit analysis dashboard (Linux / Raspberry Pi).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Prefer the local virtual environment created by install_requirements.sh.
VENV_PY="${SCRIPT_DIR}/.venv/bin/python"
if [ -x "${VENV_PY}" ]; then
    PYTHON_CMD="${VENV_PY}"
elif command -v python3 >/dev/null 2>&1; then
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
