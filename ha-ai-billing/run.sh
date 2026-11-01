#!/usr/bin/env bash
# Entry point for both deployment modes:
#  * HA add-on: Supervisor mounts /data with options.json -> convert & run
#  * Standalone (docker compose): /data absent -> run app.main directly
set -euo pipefail

export PYTHONPATH=/app

if [ -f /data/options.json ]; then
    export CONFIG_PATH="${CONFIG_PATH:-/data/config.yaml}"
    EXPORTS="$(python3 /app/addon/options2config.py /data/options.json "$CONFIG_PATH")"
    # shellcheck disable=SC2046
    set -a
    eval "$EXPORTS"
    set +a
    cd /data
fi

exec python3 -m app.main
