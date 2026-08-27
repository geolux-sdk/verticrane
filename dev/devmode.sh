#!/usr/bin/env bash
# Run a developer tool with the recorder out of the way, then put it back.
#
#   ./dev/devmode.sh                                   drop to a shell
#   ./dev/devmode.sh .venv/bin/python read_status.py   run one command
#   ./dev/devmode.sh ./dev/run_dashboard.sh            bring up the dashboard
#
# The recorder owns the serial port, so nothing here can talk to the sensor
# while it is running. Stopping it by hand is easy; forgetting to start it again
# is easier, and a field device that quietly stopped recording is the worst
# outcome in this project. Hence the trap: the service comes back even if the
# command fails, or you Ctrl-C, or the shell exits.
set -uo pipefail

SERVICE="verticrane-recorder"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

was_active=0
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "${SERVICE}"; then
    was_active=1
fi

restore() {
    if [ "${was_active}" -eq 1 ]; then
        echo
        echo "Restarting ${SERVICE}..." >&2
        sudo systemctl start "${SERVICE}" || {
            echo "COULD NOT RESTART ${SERVICE} -- the device is not recording." >&2
            echo "Start it by hand: sudo systemctl start ${SERVICE}" >&2
        }
    fi
}
trap restore EXIT INT TERM

if [ "${was_active}" -eq 1 ]; then
    echo "Stopping ${SERVICE} to free the serial port..." >&2
    sudo systemctl stop "${SERVICE}"
    # systemd sends SIGTERM and the recorder finalises the file it was writing.
    sleep 2
else
    echo "${SERVICE} is not running; nothing to stop." >&2
fi

VENV_PY="${ROOT}/.venv/bin/python"
if [ -x "${VENV_PY}" ]; then
    export PATH="${ROOT}/.venv/bin:${PATH}"
fi

if [ "$#" -eq 0 ]; then
    echo "Developer shell. The recorder is stopped; exit to restart it." >&2
    "${SHELL:-/bin/bash}"
else
    "$@"
fi
