#!/usr/bin/env bash
# run_psd_history.sh — monthly refresh of the full USDA PSD history behind the Balance Sheet page.
# Re-pulls the last 3 marketing years (the ones USDA still revises) for every commodity into
# JSA.WASDE.PSD_HISTORY. Idempotent, so the catch-up run is harmless. Needs FAS_API_KEY in .env.
# The first full 1960+ load is run by hand (~1.5 h, API limit 1,000 calls/hour): etl/fetch_psd_history.py
set -uo pipefail

APP_DIR="/opt/jpsi/wasde"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/psd_history_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.psd_history.lock"
if ! flock -n 9; then
    echo "$(date -Is) another PSD history run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== PSD history refresh start $(date -Is) ==="
    set -a; . "$APP_DIR/.env"; set +a
    "$VENV/bin/python" -u etl/fetch_psd_history.py --refresh-only --workers 3
    rc=$?    # capture immediately — must precede any other command
    echo "=== PSD history refresh finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'psd_history_*.log' -mtime +90 -delete 2>/dev/null || true

exit "$rc"
