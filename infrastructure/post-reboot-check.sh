#!/bin/bash
# Sanity-check the host after a reboot. Confirms the running kernel,
# uptime, failed units, container count, storage mounts, and that the
# mergerfs pool is non-empty.

set -u

EXPECTED_KERNEL="${EXPECTED_KERNEL:-}"
EXPECTED_CONTAINERS="${EXPECTED_CONTAINERS:-}"
STORAGE_MOUNTS=("${STORAGE_MOUNTS[@]:-/mnt/disk1 /mnt/disk2 /mnt/parity /mnt/storage}")

echo "=== KERNEL${EXPECTED_KERNEL:+ (expect $EXPECTED_KERNEL)} ==="
uname -r

echo
echo "=== UPTIME ==="
uptime

echo
echo "=== FAILED SYSTEMD UNITS ==="
systemctl --failed --no-pager

echo
echo "=== DOCKER CONTAINERS${EXPECTED_CONTAINERS:+ (expect $EXPECTED_CONTAINERS running)} ==="
echo "running: $(docker ps -q | wc -l) / total: $(docker ps -aq | wc -l)"
notup=$(docker ps -a --format '{{.Names}}  {{.Status}}' | grep -v ' Up ' || true)
[[ -n "$notup" ]] && { echo "NOT running:"; echo "$notup"; } || echo "all containers Up"

echo
echo "=== STORAGE MOUNTS ==="
findmnt -no SOURCE,TARGET,FSTYPE,SIZE ${STORAGE_MOUNTS[@]} 2>/dev/null || true
if [[ -d /mnt/storage ]]; then
    echo "mergerfs pool:"
    df -h /mnt/storage | tail -1
    echo "pool sample:"
    ls /mnt/storage 2>/dev/null | head -5
fi

echo
echo "=== reboot-required flag ==="
[[ -e /var/run/reboot-required ]] && echo "  still flagged" || echo "  cleared"
