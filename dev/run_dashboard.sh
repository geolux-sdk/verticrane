#!/usr/bin/env bash
# Launch the Streamlit analysis dashboard (Linux / Raspberry Pi).
#
# Developer tool. It opens the serial port, so stop the recorder first --
#   ./dev/devmode.sh ./dev/run_dashboard.sh
# does both and restarts the recorder afterwards.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Run from the repo root so .streamlit/config.toml and config.json are found.
cd "${ROOT}"

# Prefer the local virtual environment created by install_requirements.sh.
VENV_PY="${ROOT}/.venv/bin/python"
if [ -x "${VENV_PY}" ]; then
    PYTHON_CMD="${VENV_PY}"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

if ! "${PYTHON_CMD}" -m streamlit run dev/dashboard.py; then
    echo
    echo "Failed to start the dashboard." >&2
    echo "Run ./install_requirements.sh first to install dependencies." >&2
    exit 1
fi
