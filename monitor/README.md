# System Monitor

Self-hosted telemetry + alerting daemon for a single Debian host. Collects CPU/RAM/IO/temperature/SMART data on a schedule, stores it in SQLite, and emits two kinds of Discord notifications:

- **Critical** (immediate, with cooldown): hardware safety limits — CPU thermal throttle, drive over temp, SMART FAILED, disk above 95%.
- **Anomaly** (batched into a digest): adaptive baseline-relative detection. The system learns "normal" from rolling hourly samples (≥48 needed before adaptive mode activates), then flags values that exceed `mean + 3σ` *and* a hard floor (so statistically unusual but operationally fine values don't spam the channel).

A daily report cron produces a multi-chart PNG digest (matplotlib, dark theme) summarizing the last 24 hours and posts it as a Discord embed.

## Layout

```
main.py        APScheduler loop: collect (30s), check (60s), digest (6h), report (daily 08:00), prune (03:00), SMART (hourly), baseline (hourly+5).
metrics.py     Collection + persistence. psutil for CPU/RAM/IO/temps, smartctl for drive health, du for disk usage, docker.sock for container state.
alerts.py      Critical + anomaly detection; cooldown + digest batching.
reports.py     Daily PNG chart generator (matplotlib).
config.py      Thresholds, floors, schedule, retention, palette.
cli.py         Manual ops (status / disks / baselines / report / prune).
Dockerfile     Slim Python 3.12 + smartmontools.
docker-compose.yml   privileged, pid: host, /proc /sys /dev mounted ro so psutil/smartctl see the host.
```

## Why adaptive thresholds

Static thresholds either fire constantly (set too low for a busy server) or never (set too high to be useful). Two days of hourly samples bootstrap a per-metric baseline; after that the daemon alerts only when a value is genuinely abnormal *for this host*. Floors prevent alerts on values like "CPU at 65%" that may be statistically unusual on a quiet day but are obviously fine.

## Configuration

```
SYSTEM_MON_HOOK=https://discord.com/api/webhooks/...
REPORT_HOUR=8
REPORT_MINUTE=0
DIGEST_INTERVAL_HOURS=6
DATA_DIR=/data
```

Disk paths and monitored mounts are declared in `config.py:MONITORED_DISKS`.
