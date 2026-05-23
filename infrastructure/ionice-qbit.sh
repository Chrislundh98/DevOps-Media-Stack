#!/bin/bash
# Run on boot. Drops the qBittorrent container's IO priority to idle so it
# never starves Jellyfin transcodes or rsync jobs sharing the same disks.

sleep 30
QBIT_PID=$(docker inspect --format '{{.State.Pid}}' qbittorrent 2>/dev/null)
if [[ -n "$QBIT_PID" && "$QBIT_PID" -gt 0 ]]; then
    ionice -c3 -P "$QBIT_PID"
fi
