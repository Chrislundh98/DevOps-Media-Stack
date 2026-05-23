#!/bin/bash
echo "=== TorrentLeech Daily Visitor ==="
echo "RUN_ON_BOOT=${RUN_ON_BOOT:-false}"
echo "LOGIN_MODE=${LOGIN_MODE:-false}"
echo ""

# LOGIN_MODE: spin up VNC so you can log in manually once and save the session
if [ "${LOGIN_MODE:-false}" = "true" ]; then
    echo "LOGIN MODE: Starting Xvfb + VNC on port 5900..."
    echo "Connect with any VNC client to <server-ip>:5900"
    echo "Log into TorrentLeech, then stop the container with Ctrl+C or docker compose stop"
    echo ""

    Xvfb :99 -screen 0 1280x720x24 &
    sleep 1

    x11vnc -display :99 -nopw -listen 0.0.0.0 -xkb -forever &

    export DISPLAY=:99
    chromium \
        --no-sandbox \
        --user-data-dir=/profile \
        --disable-gpu \
        --disable-dev-shm-usage \
        "https://www.torrentleech.org" &

    # Wait indefinitely — user stops the container when done
    wait
    exit 0
fi

# Optional immediate run on container start (useful for testing)
if [ "${RUN_ON_BOOT:-false}" = "true" ]; then
    echo "RUN_ON_BOOT=true — running visit immediately..."
    /app/visit.sh
    echo ""
fi

# Scheduler loop — sleep until 05:15 each day
while true; do
    NOW=$(date +%s)
    TARGET=$(date -d "today 05:15" +%s)

    # If 05:15 already passed today, schedule for tomorrow
    if [ "$NOW" -ge "$TARGET" ]; then
        TARGET=$(date -d "tomorrow 05:15" +%s)
    fi

    SLEEP=$((TARGET - NOW))
    echo "Next visit: $(date -d "@$TARGET") (sleeping ${SLEEP}s)"
    sleep "$SLEEP"

    echo ""
    echo "Running scheduled visit..."
    /app/visit.sh
done
