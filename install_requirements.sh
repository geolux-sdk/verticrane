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
    echo "python3 -m venv --system-site-packages \"${VENV_DIR}\""
    echo "${VENV_PY} -m pip install --upgrade pip"
    echo "${VENV_PY} -m pip install -r \"${REQUIREMENTS_FILE}\""
    exit 0
fi

# Create the virtual environment once.
if [ ! -x "${VENV_PY}" ]; then
    echo "Creating virtual environment in ${VENV_DIR} ..."
    # --system-site-packages so the venv can use the distro's spidev and gpiozero,
    # which the e-paper driver needs. Those are C extensions with no wheels for the
    # Pi's Python 3.13, and building them on a 415 MB Zero 2 W is not worth it when
    # apt already ships versions matched to the running kernel. Packages installed
    # into the venv still take precedence over the system ones.
    if ! python3 -m venv --system-site-packages "${VENV_DIR}"; then
        echo "Failed to create the venv. Install the venv package first:" >&2
        echo "  sudo apt install -y python3-venv python3-full" >&2
        exit 1
    fi
fi

"${VENV_PY}" -m pip install --upgrade pip
"${VENV_PY}" -m pip install -r "${REQUIREMENTS_FILE}"

# The e-paper panel prints a Korean warning. Without a Hangul font it falls
# back to English rather than drawing boxes, but the label is meant to be read
# on a Korean site, so install the font if we are allowed to.
if ! fc-list :lang=ko 2>/dev/null | grep -q .; then
    echo
    echo "No Korean font found -- the e-paper panel would fall back to English."
    if sudo -n true 2>/dev/null; then
        echo "Installing fonts-nanum..."
        sudo apt-get install -y fonts-nanum || true
    else
        echo "Install it with:  sudo apt install -y fonts-nanum"
    fi
fi

echo
echo "Python libraries installed into ${VENV_DIR}."
echo "Run the tools with the venv python, e.g.:"
echo "  ${VENV_PY} read_status.py"
echo "Or activate it first:  source .venv/bin/activate"
