#!/usr/bin/env bash
# Pull the latest code from git (Linux / Raspberry Pi).
#
#   ./update.sh         pull latest; reinstall deps only if requirements.txt changed
#   ./update.sh --deps  pull latest and always reinstall deps
#
# Do NOT run with sudo (a dependency reinstall must go into your user-owned .venv).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    echo "Do not run this with sudo." >&2
    exit 1
fi

before="$(git rev-parse HEAD)"
echo "Pulling latest changes..."
git pull --ff-only
after="$(git rev-parse HEAD)"

if [ "${before}" = "${after}" ]; then
    echo "Already up to date."
else
    echo "Updated ${before:0:7} -> ${after:0:7}."
fi

# Reinstall dependencies if requirements.txt changed in this pull, or if forced.
deps_changed=""
if git diff --name-only "${before}" "${after}" | grep -qx "requirements.txt"; then
    deps_changed="yes"
fi

if [ "${1:-}" = "--deps" ] || [ -n "${deps_changed}" ]; then
    if [ -n "${deps_changed}" ]; then
        echo "requirements.txt changed; reinstalling dependencies..."
    fi
    ./install_requirements.sh
fi

echo "Done."
