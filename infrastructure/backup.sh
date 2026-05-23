#!/bin/bash
# Daily server backup. Tars selected source trees, rotates old archives,
# logs progress, and shows live size via pv (or a fallback monitor).
# Run as root via cron.

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/srv/backups}"
DATE=$(date +%Y-%m-%d)
LOG_DIR="$BACKUP_ROOT/logs"
LOG_FILE="$LOG_DIR/backup_${DATE}.log"

SOURCES=(
    "/home/${SUDO_USER:-$USER}/automation"
    "/home/${SUDO_USER:-$USER}/docker/appdata"
    "/home/${SUDO_USER:-$USER}/docker/compose"
    "/home/${SUDO_USER:-$USER}/monitor"
    "/home/${SUDO_USER:-$USER}/portfolio"
)

EXCLUDE_ARGS=(
    --exclude='logs' --exclude='Logs' --exclude='log'
    --exclude='*.log' --exclude='*.log.*' --exclude='*.log-*'

    --exclude='cache' --exclude='Cache' --exclude='.cache'
    --exclude='GPUCache' --exclude='torrent_cache' --exclude='Transcode_Cache'

    --exclude='chrome_profile_*'
    --exclude='AutofillStates' --exclude='BrowserMetrics' --exclude='CertificateRevocation'
    --exclude='Crashpad' --exclude='FileTypePolicies' --exclude='MEIPreload'
    --exclude='OptimizationGuide' --exclude='SafeBrowsing' --exclude='SegmentationPlatform'
    --exclude='SSLErrorAssistant' --exclude='ZxcvbnData'

    --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo'
    --exclude='site-packages' --exclude='lib/python*' --exclude='lib64'
    --exclude='*.egg-info' --exclude='pyvenv.cfg'

    --exclude='MediaCover' --exclude='UpdateLogs' --exclude='updates'

    --exclude='transcodes' --exclude='Tdarr/Transcode*'

    --exclude='backup' --exclude='backups' --exclude='*.backup'

    --exclude='*.db-shm' --exclude='*.db-wal' --exclude='*.db-journal'

    --exclude='sessions' --exclude='node_modules' --exclude='.npm'
    --exclude='.git' --exclude='*.tmp' --exclude='*.temp' --exclude='*.swp'
    --exclude='.DS_Store'

    --exclude='querylog.json' --exclude='stats.db'
    --exclude='*.cached.torrent'

    # Sockets/pipes can't be archived
    --exclude='*.sock' --exclude='*.socket' --exclude='*.pid'
)

KEEP_DAYS=7

log()      { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }
log_only() { echo "[$(date '+%F %T')] $*" >> "$LOG_FILE"; }

cleanup_old_backups() {
    log "Cleaning up backups older than $KEEP_DAYS days..."
    find "$BACKUP_ROOT" -maxdepth 1 -name "*.tar.gz" -type f -mtime +$KEEP_DAYS -exec rm -v {} \; >> "$LOG_FILE" 2>&1 || true
    find "$LOG_DIR"     -maxdepth 1 -name "backup_*.log" -type f -mtime +$KEEP_DAYS -exec rm -v {} \; >> "$LOG_FILE" 2>&1 || true
}

start_size_monitor() {
    local target_dir="$1" prefix="$2"
    (
        while true; do
            if [[ -d "$target_dir" || -f "$target_dir" ]]; then
                size=$(du -sh "$target_dir" 2>/dev/null | cut -f1)
                printf "\r%s: %s     " "$prefix" "$size"
            fi
            sleep 2
        done
    ) &
    SIZE_MONITOR_PID=$!
}

stop_size_monitor() {
    if [[ -n "${SIZE_MONITOR_PID:-}" ]]; then
        kill "$SIZE_MONITOR_PID" 2>/dev/null || true
        wait "$SIZE_MONITOR_PID" 2>/dev/null || true
        printf "\r%80s\r" ""
    fi
}

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
rm -f "$LOG_FILE" 2>/dev/null || true
touch "$LOG_FILE"

trap 'stop_size_monitor' EXIT

log "=== Backup started ==="

DEST_ROOT="$BACKUP_ROOT/$DATE"
mkdir -p "$DEST_ROOT"

FAILED=0

echo "Calculating source sizes..."
TOTAL_SOURCE_SIZE=0
for SRC in "${SOURCES[@]}"; do
    if [[ -d "$SRC" ]]; then
        src_size=$(du -sb "$SRC" 2>/dev/null | cut -f1)
        TOTAL_SOURCE_SIZE=$((TOTAL_SOURCE_SIZE + src_size))
    fi
done
TOTAL_SOURCE_HUMAN=$(numfmt --to=iec-i --suffix=B $TOTAL_SOURCE_SIZE 2>/dev/null || echo "unknown")
log "Total source size (before exclusions): $TOTAL_SOURCE_HUMAN"
echo

for SRC in "${SOURCES[@]}"; do
    if [[ ! -d "$SRC" ]]; then
        log "WARNING: Source not found, skipping: $SRC"
        continue
    fi

    log_only "Backing up: $SRC"
    echo "------------------------------------------------------------"
    echo "Backing up: $SRC"
    echo "------------------------------------------------------------"

    RSYNC_EXIT=0
    rsync -rltD --delete --no-owner --no-group --info=progress2 --human-readable \
        "${EXCLUDE_ARGS[@]}" "$SRC" "$DEST_ROOT/" 2>> "$LOG_FILE" || RSYNC_EXIT=$?
    echo

    case "$RSYNC_EXIT" in
        0)  log "  Completed: $SRC" ;;
        24) log "  Completed: $SRC (some files changed during backup - normal)" ;;
        *)  log "  Failed:    $SRC (exit code: $RSYNC_EXIT)"; FAILED=1 ;;
    esac
done

[[ $FAILED -eq 1 ]] && log "WARNING: One or more rsync operations failed. Check log for details."

BACKUP_SIZE=$(du -sh "$DEST_ROOT" 2>/dev/null | cut -f1)
log "Backup size before compression: $BACKUP_SIZE"

echo
echo "------------------------------------------------------------"
echo "Creating tar.gz archive..."
echo "------------------------------------------------------------"

cd "$BACKUP_ROOT" || exit 1
BACKUP_BYTES=$(du -sb "$DATE" 2>/dev/null | cut -f1)

if command -v pv &> /dev/null; then
    if tar --exclude='*.sock' --exclude='*.socket' -cf - "$DATE" 2>> "$LOG_FILE" \
        | pv -s "$BACKUP_BYTES" -p -t -e -r \
        | gzip > "${DATE}.tar.gz"; then
        TAR_SUCCESS=true
    else
        TAR_SUCCESS=false
    fi
else
    echo "(Install 'pv' for a progress bar: sudo apt install pv)"
    start_size_monitor "$BACKUP_ROOT/${DATE}.tar.gz" "Archive size"
    if tar --exclude='*.sock' --exclude='*.socket' -czf "${DATE}.tar.gz" "$DATE" 2>> "$LOG_FILE"; then
        TAR_SUCCESS=true
    else
        TAR_SUCCESS=false
    fi
    stop_size_monitor
fi

echo

if $TAR_SUCCESS; then
    ARCHIVE_SIZE=$(du -sh "${DATE}.tar.gz" 2>/dev/null | cut -f1)
    log "Archive successful: ${DATE}.tar.gz ($ARCHIVE_SIZE)"
    rm -rf "$DATE"
    cleanup_old_backups
    log "Backup completed: ${DATE}.tar.gz ($ARCHIVE_SIZE)"
else
    log "CRITICAL ERROR: Archive failed. Keeping uncompressed folder."
    exit 1
fi
