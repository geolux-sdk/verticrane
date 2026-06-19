#!/usr/bin/env bash
# Run the dashboard self-test in the project's virtual environment (Linux / Pi).
# Arguments are passed through, e.g.:  ./test.sh --seconds 10 --port /dev/ttyUSB0
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

exec "${PYTHON_CMD}" test.py "$@"
