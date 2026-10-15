#!/usr/bin/env bash
# run_wasde_etl.sh — monthly WASDE snapshot ETL, run by cron on the Droplet.
# Pulls the latest WASDE reporting period into JSA.WASDE.PSD_SNAPSHOTS.
# Scheduled for the 12th of each month at noon CT (buffer after ~11th release).
set -uo pipefail

APP_DIR="/opt/jpsi/wasde"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/wasde_etl_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.wasde_etl.lock"
if ! flock -n 9; then
    echo "$(date -Is) another ETL run in progress — skipping" >>"$LOG"
    exit 0
fi

{
    echo "=== WASDE ETL start $(date -Is) ==="
    "$VENV/bin/python" etl/fetch_wasde.py --latest
    rc=$?
    echo "=== WASDE ETL finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'wasde_etl_*.log' -mtime +60 -delete 2>/dev/null || true

exit "$rc"
