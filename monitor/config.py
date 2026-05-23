import os
from pathlib import Path

_script_dir = Path(__file__).parent.resolve()

try:
    from dotenv import load_dotenv
    load_dotenv(_script_dir / ".env")
except ImportError:
    pass

DATA_DIR = Path(os.getenv("DATA_DIR", _script_dir / "data"))
DB_PATH = DATA_DIR / "metrics.db"

DISCORD_WEBHOOK = os.getenv("SYSTEM_MON_HOOK", "")

COLLECT_INTERVAL = 30
ALERT_CHECK_INTERVAL = 60

# ─── Alert philosophy ───
# Two tiers:
#   1. CRITICAL (immediate): Hardware safety limits that risk damage or data loss.
#   2. ANOMALY (batched digest): Adaptive baseline-relative detection.
#      System learns "normal" from hourly baselines over time, then alerts
#      when values spike significantly above that learned baseline.
#      All anomalies batch into ONE Discord embed per digest cycle.

# ─── Critical thresholds (immediate, non-negotiable) ───
CRIT_CPU_TEMP = 100       # Intel thermal throttle
CRIT_HDD_TEMP = 65        # WD Red Pro absolute rated max
CRIT_NVME_TEMP = 80       # NVMe thermal throttle point
CRIT_DISK_PCT = 95        # Writes can fail
CRIT_SMART_FAILURE = True

# ─── Chart threshold lines (used by reports.py) ───
TEMP_WARN_CPU = 90
TEMP_WARN_SYSTEM = 90
IOWAIT_WARN = 30
IOWAIT_CRIT = 50

# ─── Adaptive anomaly detection ───
ANOMALY_WINDOW = 600           # 10-min rolling window for current state
ANOMALY_SIGMA = 3.0            # Std devs above baseline mean to flag as anomaly
ANOMALY_MIN_BASELINES = 48     # Need ~2 days of hourly data before adaptive mode

# Fallback thresholds during learning period (deliberately generous)
FALLBACK_CPU_TEMP = 95
FALLBACK_HDD_TEMP = 62
FALLBACK_IOWAIT = 60
FALLBACK_SWAP_GB = 12.0
FALLBACK_CPU_PCT = 98
FALLBACK_RAM_PCT = 96

# ─── Anomaly minimum floors ───
# Below these absolute values, NO anomaly is raised regardless of baseline deviation.
# This prevents noise from statistically "unusual" values that are perfectly healthy.
# Tuned for: i5-13500, 64GB DDR5, Noctua NH-L12S Ghost (SFF), 3x WD Red Pro, 1TB NVMe
FLOOR_CPU_TEMP = 75            # SFF cooling is fine under 75°C
FLOOR_CPU_PCT = 60             # 16 threads, heavy services — 60% is working, not warning
FLOOR_RAM_PCT = 70             # 64GB = ~45GB used before this even triggers
FLOOR_IOWAIT = 30              # 3 HDDs + transcoding + torrents = I/O is expected
FLOOR_SWAP_GB = 6.0            # Some swap with 64GB RAM is completely normal
FLOOR_HDD_TEMP = 50            # WD Red Pro rated to 65°C, 50 is warm but fine
FLOOR_NVME_TEMP = 65           # Throttles at 80°C, 65 is normal under load

# ─── Digest settings ───
DIGEST_INTERVAL_HOURS = int(os.getenv("DIGEST_INTERVAL_HOURS", "6"))

# ─── Data retention ───
BASELINE_RETENTION_DAYS = 14
RETENTION_HOURS = 48

# ─── Disk monitoring ───
DISK_WARN = 85
DISK_CRIT = 95

MONITORED_DISKS = [
    {"path": "/mnt/disk1", "name": "Disk 1 (15T)"},
    {"path": "/mnt/disk2", "name": "Disk 2 (20T)"},
    {"path": "/mnt/parity", "name": "Parity (24T)"},
    {"path": "/", "name": "NVMe (OS)"},
]

REPORT_HOUR = int(os.getenv("REPORT_HOUR", "8"))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "0"))

CHART_STYLE = {
    "bg_color": "#1a1a2e",
    "fg_color": "#eaeaea",
    "grid_color": "#2d2d44",
    "accent_colors": ["#00d4ff", "#ff6b6b", "#4ecdc4", "#ffe66d", "#c44dff", "#7dd87d"],
    "warn_color": "#ffaa00",
    "crit_color": "#ff4444",
}
