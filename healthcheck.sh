#!/usr/bin/env bash
# Restart the dashboard service if its HTTP health endpoint stops responding.
# Catches the "process alive but unresponsive" case that Restart=always misses.
# Run periodically by verticrane-healthcheck.timer (needs root to call systemctl).
set -uo pipefail

URL="http://localhost:8501/_stcore/health"
SERVICE="verticrane-dashboard"

# Two attempts before deciding it's down, so a transient blip doesn't restart it.
for _ in 1 2; do
    if curl -fsS --max-time 5 "${URL}" >/dev/null 2>&1; then
        exit 0
    fi
    sleep 3
done

echo "Health check failed (${URL}); restarting ${SERVICE}"
systemctl restart "${SERVICE}"
