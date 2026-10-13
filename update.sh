#!/usr/bin/env bash
# Pull the latest code from git (Linux / Raspberry Pi).
#
#   ./update.sh         pull latest; reinstall deps only if requirements.txt changed
#   ./update.sh --deps  pull latest and always reinstall deps
#   ./update.sh --dev   pull latest and install runtime + developer dependencies
#
# Do NOT run with sudo (a dependency reinstall must go into your user-owned .venv).
set -euo pipefail

force_deps=0
dev_args=()
for arg in "$@"; do
    case "$arg" in
        --deps) force_deps=1 ;;
        --dev) force_deps=1; dev_args=(--dev) ;;
        *) echo "Usage: $0 [--deps] [--dev]" >&2; exit 2 ;;
    esac
done

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

if [ "${force_deps}" -eq 1 ] || [ -n "${deps_changed}" ]; then
    if [ -n "${deps_changed}" ]; then
        echo "requirements.txt changed; reinstalling dependencies..."
    fi
    ./install_requirements.sh "${dev_args[@]}"
fi

echo "Done."
