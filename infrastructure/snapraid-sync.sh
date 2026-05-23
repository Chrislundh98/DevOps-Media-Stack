#!/bin/bash
# Safe SnapRAID sync wrapper for cron. Runs preflight checks before sync,
# bails on suspicious diffs, waits for active writers, and optionally
# scrubs a percentage of stale data afterward. Notifies via Discord.
#
# Exits cleanly when there is nothing to sync; aborts (and notifies) when
# the diff or environment looks unsafe.

set -euo pipefail

# Configuration

SNAPRAID_BIN="${SNAPRAID_BIN:-/usr/bin/snapraid}"
SNAPRAID_CONF="${SNAPRAID_CONF:-/etc/snapraid.conf}"
LOCKFILE="${LOCKFILE:-/tmp/snapraid-sync.lock}"
LOG_DIR="${LOG_DIR:-/var/log/snapraid}"
LOG_FILE="${LOG_DIR}/sync-$(date +%Y%m%d-%H%M%S).log"

DELETE_THRESHOLD="${DELETE_THRESHOLD:-150000}"
UPDATE_THRESHOLD="${UPDATE_THRESHOLD:-500}"
REMOVED_THRESHOLD_PCT="${REMOVED_THRESHOLD_PCT:-10}"

MOUNT_POINTS=()

# Load from environment; never hardcode in source.
DISCORD_WEBHOOK="${DISCORD_WEBHOOK:-}"

SCRUB_ENABLED="${SCRUB_ENABLED:-true}"
SCRUB_PERCENTAGE="${SCRUB_PERCENTAGE:-5}"
SCRUB_AGE="${SCRUB_AGE:-10}"

LOG_RETENTION="${LOG_RETENTION:-30}"

WRITER_PROCESSES=("ffmpeg" "tdarr" "HandBrakeCLI" "qbittorrent-nox")
DATA_DISKS=("/mnt/disk1" "/mnt/disk2")
WRITE_CHECK_MAX_WAIT="${WRITE_CHECK_MAX_WAIT:-1800}"
WRITE_CHECK_INTERVAL="${WRITE_CHECK_INTERVAL:-60}"


log() {
    local level="$1"; shift
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

notify() {
    local message="$1"
    local status="${2:-info}"

    [[ -z "$DISCORD_WEBHOOK" ]] && return

    local color
    case "$status" in
        success) color=3066993 ;;
        error)   color=15158332 ;;
        warning) color=16776960 ;;
        *)       color=3447003 ;;
    esac

    curl -s -o /dev/null -H "Content-Type: application/json" \
        -d "{\"embeds\":[{\"title\":\"SnapRAID Sync\",\"description\":\"${message}\",\"color\":${color}}]}" \
        "$DISCORD_WEBHOOK" 2>/dev/null || true
}

cleanup() {
    rm -f "$LOCKFILE"
    log "INFO" "Lock released"
}

abort() {
    log "ERROR" "$1"
    notify "$1" "error"
    exit 1
}

parse_mount_points() {
    [[ ${#MOUNT_POINTS[@]} -gt 0 ]] && return

    [[ -f "$SNAPRAID_CONF" ]] || abort "Config file not found: ${SNAPRAID_CONF}"

    local points=()
    while IFS= read -r line; do
        local keyword path
        keyword=$(echo "$line" | awk '{print $1}')
        path=$(echo "$line" | awk '{print $NF}' | sed 's/\s*$//')

        # parity entries are file paths; convert to their directory.
        if [[ "$keyword" == "parity" || "$keyword" =~ ^[0-9]+-parity$ ]]; then
            path=$(dirname "$path")
        fi

        points+=("$path")
    done < <(grep -E '^\s*(data|parity|[0-9]+-parity)\s' "$SNAPRAID_CONF" | grep -v '^\s*#')

    [[ ${#points[@]} -gt 0 ]] || abort "No data/parity paths found in ${SNAPRAID_CONF}"
    MOUNT_POINTS=("${points[@]}")
}


# Preflight checks

preflight_binary() {
    [[ -x "$SNAPRAID_BIN" ]] || abort "SnapRAID binary not found or not executable: ${SNAPRAID_BIN}"
    log "INFO" "Binary OK: ${SNAPRAID_BIN}"
}

preflight_config() {
    [[ -f "$SNAPRAID_CONF" ]] || abort "Config not found: ${SNAPRAID_CONF}"
    log "INFO" "Config OK: ${SNAPRAID_CONF}"
}

preflight_lock() {
    if [[ -f "$LOCKFILE" ]]; then
        local lock_pid
        lock_pid=$(cat "$LOCKFILE" 2>/dev/null || echo "")

        if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
            log "WARNING" "Another instance is running (PID: ${lock_pid}), exiting"
            notify "Sync skipped — already running (PID: ${lock_pid})" "warning"
            exit 0
        else
            log "WARNING" "Stale lock found (PID: ${lock_pid}), removing"
            rm -f "$LOCKFILE"
        fi
    fi

    echo $$ > "$LOCKFILE"
    trap cleanup EXIT
    log "INFO" "Lock acquired (PID: $$)"
}

preflight_disks() {
    parse_mount_points

    for mount in "${MOUNT_POINTS[@]}"; do
        [[ -d "$mount" ]] || abort "Mount point does not exist: ${mount}"

        # Guard against an unmounted disk silently syncing against root.
        if ! mountpoint -q "$mount" 2>/dev/null; then
            if ! findmnt -rn -o TARGET --target "$mount" > /dev/null 2>&1; then
                local parent_mount
                parent_mount=$(stat -c '%m' "$mount" 2>/dev/null || echo "/")
                if [[ "$parent_mount" == "/" && "$mount" != "/" ]]; then
                    abort "Disk not mounted: ${mount} (would sync against root filesystem)"
                fi
            fi
        fi

        [[ -r "$mount" ]] || abort "Mount point not readable: ${mount}"
        log "INFO" "Disk OK: ${mount}"
    done
}

preflight_smart() {
    if ! command -v smartctl &>/dev/null; then
        log "WARNING" "smartctl not installed — skipping SMART check"
        return
    fi

    local smart_output fail_count
    smart_output=$("$SNAPRAID_BIN" -c "$SNAPRAID_CONF" smart 2>&1) || true
    fail_count=$(echo "$smart_output" | grep -ci "FAIL\|BAD" || true)

    if [[ "$fail_count" -gt 0 ]]; then
        log "WARNING" "SMART reports ${fail_count} potential disk issue(s)"

        local details=""
        for dev in /dev/sd?; do
            local dev_issues
            dev_issues=$(smartctl -A "$dev" 2>/dev/null | grep -iE "Reallocated|Pending|Uncorrect" | grep -v "^$" || true)
            [[ -n "$dev_issues" ]] && details="${details}\\n**${dev}:**\\n\`\`\`${dev_issues}\`\`\`"
        done

        notify "SMART detected ${fail_count} issue(s) — sync will proceed\\n${details}" "warning"
    else
        log "INFO" "SMART OK"
    fi
}

preflight_active_writes() {
    local waited=0

    while [[ "$waited" -lt "$WRITE_CHECK_MAX_WAIT" ]]; do
        local writers=""

        for proc in "${WRITER_PROCESSES[@]}"; do
            local pids
            pids=$(pgrep -f "$proc" 2>/dev/null || true)
            [[ -z "$pids" ]] && continue

            for pid in $pids; do
                [[ -d "/proc/$pid/fd" ]] || continue

                local has_write=false
                for disk in "${DATA_DISKS[@]}"; do
                    if ls -l "/proc/$pid/fd" 2>/dev/null | grep -q "$disk"; then
                        has_write=true
                        break
                    fi
                done

                if [[ "$has_write" == true ]]; then
                    writers="${writers:+$writers, }${proc}(${pid})"
                    break
                fi
            done
        done

        if [[ -z "$writers" ]]; then
            [[ "$waited" -gt 0 ]] && log "INFO" "Writers finished after ${waited}s wait" \
                                  || log "INFO" "No active writers on data disks"
            return 0
        fi

        if [[ "$waited" -eq 0 ]]; then
            log "INFO" "Active writers detected: ${writers} — waiting up to ${WRITE_CHECK_MAX_WAIT}s"
            notify "Sync waiting for active writers: ${writers}" "info"
        fi

        sleep "$WRITE_CHECK_INTERVAL"
        waited=$((waited + WRITE_CHECK_INTERVAL))
    done

    log "WARNING" "Writers still active after ${WRITE_CHECK_MAX_WAIT}s — proceeding anyway"
    notify "Sync proceeding despite active writers (waited ${WRITE_CHECK_MAX_WAIT}s)" "warning"
    return 0
}


# Diff analysis

run_diff() {
    log "INFO" "Running diff..."

    local diff_output
    diff_output=$("$SNAPRAID_BIN" -c "$SNAPRAID_CONF" diff 2>&1) || true
    echo "$diff_output" >> "$LOG_FILE"

    local added=0 removed=0 updated=0 moved=0 copied=0 restored=0

    while IFS= read -r line; do
        case "$line" in
            *"add"*)     added=$(echo "$line" | grep -oP '^\s*\K\d+') ;;
            *"remove"*)  removed=$(echo "$line" | grep -oP '^\s*\K\d+') ;;
            *"update"*)  updated=$(echo "$line" | grep -oP '^\s*\K\d+') ;;
            *"move"*)    moved=$(echo "$line" | grep -oP '^\s*\K\d+') ;;
            *"copy"*)    copied=$(echo "$line" | grep -oP '^\s*\K\d+') ;;
            *"restore"*) restored=$(echo "$line" | grep -oP '^\s*\K\d+') ;;
        esac
    done <<< "$diff_output"

    log "INFO" "Diff: +${added} -${removed} ~${updated} moved:${moved} copied:${copied} restored:${restored}"

    if [[ "$removed" -gt "$DELETE_THRESHOLD" ]]; then
        abort "Delete threshold exceeded: ${removed} removals (threshold: ${DELETE_THRESHOLD}). Manual intervention required."
    fi

    if [[ "$updated" -gt "$UPDATE_THRESHOLD" ]]; then
        abort "Update threshold exceeded: ${updated} updates (threshold: ${UPDATE_THRESHOLD}). Manual intervention required."
    fi

    local total_files
    total_files=$("$SNAPRAID_BIN" -c "$SNAPRAID_CONF" status 2>&1 | grep -oP 'files\s+\K\d+' | head -1 || echo "0")

    if [[ "$total_files" -gt 0 && "$removed" -gt 0 ]]; then
        local pct=$((removed * 100 / total_files))
        if [[ "$pct" -ge "$REMOVED_THRESHOLD_PCT" ]]; then
            abort "Removal percentage too high: ${pct}% of files (${removed}/${total_files}, threshold: ${REMOVED_THRESHOLD_PCT}%). Manual intervention required."
        fi
    fi

    if [[ "$added" -eq 0 && "$removed" -eq 0 && "$updated" -eq 0 \
       && "$moved" -eq 0 && "$copied" -eq 0 && "$restored" -eq 0 ]]; then
        log "INFO" "No changes detected, nothing to sync"
        notify "No changes to sync" "info"
        return 1
    fi

    return 0
}


# Sync & scrub

run_sync() {
    log "INFO" "Starting sync..."
    local start_time
    start_time=$(date +%s)

    if "$SNAPRAID_BIN" -c "$SNAPRAID_CONF" sync >> "$LOG_FILE" 2>&1; then
        local duration=$(( $(date +%s) - start_time ))
        log "INFO" "Sync completed in $(( duration / 60 ))m $(( duration % 60 ))s"
        notify "Sync completed in $(( duration / 60 ))m $(( duration % 60 ))s" "success"
        return 0
    fi

    local duration=$(( $(date +%s) - start_time ))
    local exit_code=$?
    log "ERROR" "Sync failed after $(( duration / 60 ))m $(( duration % 60 ))s (exit: ${exit_code})"

    local file_errors
    file_errors=$(grep -c "Unexpected size change" "$LOG_FILE" 2>/dev/null || echo "0")
    if [[ "$file_errors" -gt 0 ]]; then
        local changed_files
        changed_files=$(grep "Unexpected size change" "$LOG_FILE" | grep -oP "file '\K[^']+(?=')" | sort -u || true)
        abort "Sync failed — ${file_errors} file(s) changed during sync:\\n${changed_files}\\nWait for active writes to finish, then re-run."
    fi

    abort "Sync failed — check log: ${LOG_FILE}"
}

run_scrub() {
    [[ "$SCRUB_ENABLED" == true ]] || return

    log "INFO" "Starting scrub (${SCRUB_PERCENTAGE}% of data older than ${SCRUB_AGE} days)..."

    if "$SNAPRAID_BIN" -c "$SNAPRAID_CONF" -p "$SCRUB_PERCENTAGE" -o "$SCRUB_AGE" scrub >> "$LOG_FILE" 2>&1; then
        log "INFO" "Scrub completed"
    else
        log "WARNING" "Scrub failed (non-fatal)"
        notify "Scrub failed — check log" "warning"
    fi
}

cleanup_logs() {
    [[ -d "$LOG_DIR" ]] || return
    find "$LOG_DIR" -name "sync-*.log" -mtime +"$LOG_RETENTION" -delete 2>/dev/null || true
}


main() {
    mkdir -p "$LOG_DIR"

    log "INFO" "=== SnapRAID Safe Sync Started ==="

    preflight_binary
    preflight_config
    preflight_lock
    preflight_disks
    preflight_smart
    preflight_active_writes

    if run_diff; then
        run_sync
        run_scrub
    fi

    cleanup_logs

    log "INFO" "=== SnapRAID Safe Sync Finished ==="
}

main "$@"
