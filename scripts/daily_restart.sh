#!/bin/bash
#
# Daily pre-market restart of trade-engine.service.
#
# Runs at 08:50 IST (= 03:20 UTC, server is UTC).
# Goal: cycle the long-running process before market open so any slow leak
# or stale WebSocket from yesterday is wiped.
#
# Logs to /var/log/trade-engine-restart.log (rotated daily by logrotate).
#
# Invocation:
#   • systemd timer:  trade-engine-restart.timer (preferred)
#   • cron (root):    20 3 * * *  /home/kumarasamyppm321/auto-trade/scripts/daily_restart.sh

set -uo pipefail

SERVICE="trade-engine.service"
LOG_FILE="/var/log/trade-engine-restart.log"
STATUS_URL="http://127.0.0.1:8000/auto-trading/status"
MAX_WAIT_S=30

# ---- logging helper ---------------------------------------------------------
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null
log() {
    local ts
    ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    echo "$ts | $*" | tee -a "$LOG_FILE"
}

# ---- guard: skip restart on Sat/Sun (markets closed) ------------------------
DOW=$(date -u +%u)   # 1=Mon … 7=Sun
if [[ "$DOW" == "6" || "$DOW" == "7" ]]; then
    log "Weekend (dow=$DOW) — skipping restart"
    exit 0
fi

log "=== Daily restart starting ==="

# ---- pre-restart status ----------------------------------------------------
if systemctl is-active --quiet "$SERVICE"; then
    log "Service is currently active — proceeding with restart"
else
    log "Service is INACTIVE — will (re)start"
fi

# ---- restart ----------------------------------------------------------------
if systemctl restart "$SERVICE"; then
    log "systemctl restart returned 0"
else
    rc=$?
    log "ERROR: systemctl restart exited $rc"
    exit "$rc"
fi

# ---- wait for HTTP readiness -----------------------------------------------
elapsed=0
while (( elapsed < MAX_WAIT_S )); do
    if curl -sf -o /dev/null -m 2 "$STATUS_URL"; then
        log "Health check OK after ${elapsed}s ($STATUS_URL)"
        log "=== Daily restart complete ==="
        exit 0
    fi
    sleep 2
    elapsed=$(( elapsed + 2 ))
done

log "ERROR: service did not respond to $STATUS_URL within ${MAX_WAIT_S}s"
log "Last 20 journal lines:"
journalctl -u "$SERVICE" -n 20 --no-pager 2>&1 | tee -a "$LOG_FILE"
exit 1
