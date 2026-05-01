#!/bin/bash

# Prevent macOS from sleeping while server runs
caffeinate -i &
CAFFEINATE_PID=$!

# Keep network alive by pinging every 30s in background
keep_network_alive() {
    while true; do
        ping -c 1 -q 8.8.8.8 > /dev/null 2>&1
        sleep 30
    done
}
keep_network_alive &
PING_PID=$!

cleanup() {
    echo ""
    echo "Shutting down..."
    kill $CAFFEINATE_PID 2>/dev/null
    kill $PING_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "Server starting (sleep prevention active)..."
cd "$(dirname "$0")"
LOG_FILE="server.log"
echo "--- Server started at $(date) ---" >> "$LOG_FILE"
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload 2>&1 | tee -a "$LOG_FILE"
cleanup
