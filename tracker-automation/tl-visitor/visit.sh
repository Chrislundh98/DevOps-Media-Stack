#!/bin/bash
echo "[$(date)] Starting visit..."

# Inject cookies from tl_cookie.json into the Chromium profile before launch
python3 /app/setup_cookies.py || exit 1

# Clean up stale lock files from previous runs
rm -f /tmp/.X99-lock
rm -f /profile/Singleton{Lock,Socket,Cookie}
rm -f /profile/Default/.org.chromium.Chromium.*

Xvfb :99 -screen 0 1920x1080x24 -ac &
XVFB_PID=$!
sleep 2

export DISPLAY=:99

chromium \
    --no-sandbox \
    --user-data-dir=/profile \
    --disable-dev-shm-usage \
    --password-store=basic \
    --disable-blink-features=AutomationControlled \
    --disable-gpu-sandbox \
    --window-size=1920,1080 \
    --no-first-run \
    --disable-infobars \
    --app="https://www.torrentleech.org" &
CHROME_PID=$!

echo "[$(date)] Browser launched, waiting 60s for visit to register..."
sleep 60

kill "$CHROME_PID" 2>/dev/null
wait "$CHROME_PID" 2>/dev/null
kill "$XVFB_PID" 2>/dev/null
wait "$XVFB_PID" 2>/dev/null

echo "[$(date)] Visit complete."
