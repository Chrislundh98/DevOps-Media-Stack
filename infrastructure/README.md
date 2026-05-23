# Infrastructure Scripts

Operational scripts for the home server. All run under cron or systemd, all assume Debian.

## Contents

| Script | Purpose |
| --- | --- |
| `backup.sh` | Daily rsync + tar.gz of automation, docker compose/appdata, monitoring, and portfolio trees. Live progress via `pv`, 7-day rotation, hard exclude list for caches and Chrome profile junk. |
| `snapraid-sync.sh` | Cron-safe SnapRAID wrapper. Preflight: binary, config, lockfile, disk mountpoints (refuses to sync against `/` if a disk is missing), SMART, active-writer detection. Aborts on suspicious diffs (e.g. >150k removals or >10% of files gone) and notifies via Discord. Optional scrub afterward. |
| `fix_audio.sh` | Walks the MKV/MP4 library, ensures the English audio track is flagged as default. Caches per-file mtime so re-runs are cheap. Uses `mkvtoolnix` in a Docker container. |
| `ionice-qbit.sh` | Runs on boot, drops the qBittorrent container's IO priority to `idle` so it never starves Jellyfin transcodes or live rsync jobs. |
| `ownership.sh` | Resets `1000:1000` ownership across the docker / data / portfolio trees after a root-uid container has touched them. |
| `post-reboot-check.sh` | Quick sanity check after an unattended reboot: kernel, uptime, failed units, container count, mount status, mergerfs pool non-empty, reboot-required flag cleared. |

## Conventions

- Everything is parameterizable via env vars. Defaults work on the canonical host but the scripts are not host-coupled.
- Discord webhooks are read from env, never hardcoded.
- Logs go to a script-local `logs/` (or `/var/log/snapraid` for SnapRAID) and rotate themselves.
- Lockfiles are PID-based, and stale locks are detected and cleared.
