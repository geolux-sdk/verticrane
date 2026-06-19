#!/usr/bin/env bash
# Install Python dependencies for the HWT9037-485 tooling (Linux / Raspberry Pi).
#
# Recent Raspberry Pi OS / Debian (PEP 668) block installing into the system Python,
# so this creates a local virtual environment (.venv) and installs into that.
# Do NOT run this with sudo -- the venv must be owned by your user.
#
#   ./install_requirements.sh            create .venv and install
#   ./install_requirements.sh --dry-run  print the commands without running them
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
VENV_DIR="${SCRIPT_DIR}/.venv"
VENV_PY="${VENV_DIR}/bin/python"

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    echo "Do not run this with sudo; the virtual environment must be owned by your user." >&2
    exit 1
fi

if [ ! -f "${REQUIREMENTS_FILE}" ]; then
    echo "requirements.txt was not found." >&2
    exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
    echo "python3 -m venv \"${VENV_DIR}\""
    echo "${VENV_PY} -m pip install --upgrade pip"
    echo "${VENV_PY} -m pip install -r \"${REQUIREMENTS_FILE}\""
    exit 0
fi

# Create the virtual environment once.
if [ ! -x "${VENV_PY}" ]; then
    echo "Creating virtual environment in ${VENV_DIR} ..."
    if ! python3 -m venv "${VENV_DIR}"; then
        echo "Failed to create the venv. Install the venv package first:" >&2
        echo "  sudo apt install -y python3-venv python3-full" >&2
        exit 1
    fi
fi

"${VENV_PY}" -m pip install --upgrade pip
"${VENV_PY}" -m pip install -r "${REQUIREMENTS_FILE}"

echo
echo "Python libraries installed into ${VENV_DIR}."
echo "Run the tools with the venv python, e.g.:"
echo "  ${VENV_PY} read_status.py"
echo "Or activate it first:  source .venv/bin/activate"
