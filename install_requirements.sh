#!/usr/bin/env bash
# Install Python dependencies for the HWT9037-485 tooling (Linux / Raspberry Pi).
#   ./install_requirements.sh            install
#   ./install_requirements.sh --dry-run  print the commands without running them
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"

# Prefer python3; fall back to python.
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

if [ ! -f "${REQUIREMENTS_FILE}" ]; then
    echo "requirements.txt was not found." >&2
    exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
    echo "${PYTHON_CMD} -m pip install --upgrade pip"
    echo "${PYTHON_CMD} -m pip install -r \"${REQUIREMENTS_FILE}\""
    exit 0
fi

"${PYTHON_CMD}" -m pip install --upgrade pip
"${PYTHON_CMD}" -m pip install -r "${REQUIREMENTS_FILE}"

echo "Python libraries installed successfully."
