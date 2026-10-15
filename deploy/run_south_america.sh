#!/usr/bin/env bash
# run_south_america.sh — monthly CONAB (Brazil) reload, run by cron on the Droplet.
# Re-downloads CONAB's soy/corn/wheat historical-series workbooks and replaces
# JSA.WASDE.CONAB_SERIES. Idempotent, so the catch-up run is harmless.
# MAGyP (Argentina) is NOT loaded here: the Droplet cannot reach datos.magyp.gob.ar (connect
# timeout, verified 2026-10-14). Refresh it from a desktop with `etl/fetch_south_america.py --only magyp`.
# CONAB publishes its monthly crop survey around the 10th-15th and refreshes the workbooks
# the same day or a day later; cron runs on the 16th with a catch-up on the 22nd.
# BAGE has no feed (bot-protected site); its numbers are typed in on the dashboard.
set -uo pipefail

APP_DIR="/opt/jpsi/wasde"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/south_america_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.south_america.lock"
if ! flock -n 9; then
    echo "$(date -Is) another South America run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== South America ETL start $(date -Is) ==="
    git pull --ff-only || echo "git pull failed — continuing with the code on disk"
    "$VENV/bin/python" etl/fetch_south_america.py --only conab
    rc=$?    # capture immediately — must precede any other command
    echo "=== South America ETL finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'south_america_*.log' -mtime +90 -delete 2>/dev/null || true

exit "$rc"
