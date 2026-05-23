import sqlite3
import os
import time
import logging
import subprocess
import json
import shutil
import math
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

import psutil

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False

import config

log = logging.getLogger(__name__)

def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS system_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                cpu_percent REAL,
                ram_percent REAL,
                ram_used_gb REAL,
                ram_total_gb REAL,
                iowait REAL,
                swap_used_gb REAL,
                swap_total_gb REAL
            );

            CREATE TABLE IF NOT EXISTS cpu_temps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                core_id TEXT NOT NULL,
                temp REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_temps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                sensor_name TEXT NOT NULL,
                temp REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS container_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                container_name TEXT NOT NULL,
                cpu_percent REAL,
                ram_mb REAL,
                ram_percent REAL
            );

            CREATE TABLE IF NOT EXISTS disk_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                disk_name TEXT NOT NULL,
                mount_path TEXT NOT NULL,
                used_gb REAL,
                total_gb REAL,
                percent_used REAL
            );

            CREATE TABLE IF NOT EXISTS disk_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                device TEXT NOT NULL,
                model TEXT,
                serial TEXT,
                temperature REAL,
                power_on_hours INTEGER,
                health_status TEXT,
                smart_data TEXT
            );

            CREATE TABLE IF NOT EXISTS disk_io (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                device TEXT NOT NULL,
                read_mb_s REAL,
                write_mb_s REAL,
                read_iops REAL,
                write_iops REAL,
                busy_pct REAL
            );

            CREATE TABLE IF NOT EXISTS hourly_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour_bucket INTEGER NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                UNIQUE(hour_bucket, metric)
            );

            CREATE INDEX IF NOT EXISTS idx_system_ts ON system_metrics(timestamp);
            CREATE INDEX IF NOT EXISTS idx_cpu_temps_ts ON cpu_temps(timestamp);
            CREATE INDEX IF NOT EXISTS idx_system_temps_ts ON system_temps(timestamp);
            CREATE INDEX IF NOT EXISTS idx_container_ts ON container_metrics(timestamp);
            CREATE INDEX IF NOT EXISTS idx_disk_ts ON disk_metrics(timestamp);
            CREATE INDEX IF NOT EXISTS idx_disk_health_ts ON disk_health(timestamp);
            CREATE INDEX IF NOT EXISTS idx_disk_io_ts ON disk_io(timestamp);
            CREATE INDEX IF NOT EXISTS idx_baselines_metric ON hourly_baselines(metric);
        """)

        _migrate_schema(conn)

def _migrate_schema(conn):
    cursor = conn.execute("PRAGMA table_info(system_metrics)")
    columns = {row[1] for row in cursor.fetchall()}

    migrations = {
        "iowait": "ALTER TABLE system_metrics ADD COLUMN iowait REAL",
        "swap_used_gb": "ALTER TABLE system_metrics ADD COLUMN swap_used_gb REAL",
        "swap_total_gb": "ALTER TABLE system_metrics ADD COLUMN swap_total_gb REAL",
    }

    for col, sql in migrations.items():
        if col not in columns:
            try:
                conn.execute(sql)
                log.info(f"Migrated: added {col} to system_metrics")
            except sqlite3.OperationalError:
                pass

@contextmanager
def get_db():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

_last_cpu_times = None
_last_disk_io = None
_last_disk_io_time = None

def _get_iowait() -> Optional[float]:
    global _last_cpu_times
    times = psutil.cpu_times()
    if _last_cpu_times is None:
        _last_cpu_times = times
        return None

    prev = _last_cpu_times
    _last_cpu_times = times

    total_delta = sum([
        times.user - prev.user,
        times.system - prev.system,
        times.idle - prev.idle,
        times.iowait - prev.iowait,
        getattr(times, 'irq', 0) - getattr(prev, 'irq', 0),
        getattr(times, 'softirq', 0) - getattr(prev, 'softirq', 0),
        getattr(times, 'steal', 0) - getattr(prev, 'steal', 0),
    ])

    if total_delta <= 0:
        return 0.0

    return ((times.iowait - prev.iowait) / total_delta) * 100

def _get_disk_io() -> dict:
    global _last_disk_io, _last_disk_io_time

    current = psutil.disk_io_counters(perdisk=True)
    now = time.time()

    if _last_disk_io is None or _last_disk_io_time is None:
        _last_disk_io = current
        _last_disk_io_time = now
        return {}

    dt = now - _last_disk_io_time
    if dt <= 0:
        return {}

    result = {}
    for dev in ["sda", "sdb", "sdc"]:
        if dev not in current or dev not in _last_disk_io:
            continue

        cur = current[dev]
        prev = _last_disk_io[dev]

        read_bytes = cur.read_bytes - prev.read_bytes
        write_bytes = cur.write_bytes - prev.write_bytes
        read_count = cur.read_count - prev.read_count
        write_count = cur.write_count - prev.write_count
        busy_ms = cur.busy_time - prev.busy_time

        result[dev] = {
            "read_mb_s": (read_bytes / dt) / (1024 * 1024),
            "write_mb_s": (write_bytes / dt) / (1024 * 1024),
            "read_iops": read_count / dt,
            "write_iops": write_count / dt,
            "busy_pct": min((busy_ms / (dt * 1000)) * 100, 100),
        }

    _last_disk_io = current
    _last_disk_io_time = now
    return result

def collect_metrics():
    ts = time.time()

    iowait = _get_iowait()
    disk_io = _get_disk_io()

    with get_db() as conn:
        cpu_percent = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        conn.execute(
            """INSERT INTO system_metrics
               (timestamp, cpu_percent, ram_percent, ram_used_gb, ram_total_gb, iowait, swap_used_gb, swap_total_gb)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, cpu_percent, mem.percent, mem.used / (1024**3), mem.total / (1024**3),
             iowait, swap.used / (1024**3), swap.total / (1024**3))
        )

        temps = psutil.sensors_temperatures()

        if "coretemp" in temps:
            for sensor in temps["coretemp"]:
                if sensor.current > 0:
                    conn.execute(
                        "INSERT INTO cpu_temps (timestamp, core_id, temp) VALUES (?, ?, ?)",
                        (ts, sensor.label or f"Core {sensor.label}", sensor.current)
                    )

        for sensor_type in ["acpitz", "nvme", "iwlwifi_1", "pch_cometlake", "pch_cannonlake", "drivetemp"]:
            if sensor_type in temps:
                for sensor in temps[sensor_type]:
                    if sensor.current > 0:
                        label = sensor.label or "temp"
                        if sensor_type == "drivetemp":
                            label = _resolve_drivetemp_label(sensor)
                        conn.execute(
                            "INSERT INTO system_temps (timestamp, sensor_name, temp) VALUES (?, ?, ?)",
                            (ts, f"{sensor_type}:{label}", sensor.current)
                        )

        for disk_conf in config.MONITORED_DISKS:
            try:
                usage = psutil.disk_usage(disk_conf["path"])
                conn.execute(
                    "INSERT INTO disk_metrics (timestamp, disk_name, mount_path, used_gb, total_gb, percent_used) VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, disk_conf["name"], disk_conf["path"], usage.used / (1024**3), usage.total / (1024**3), usage.percent)
                )
            except (FileNotFoundError, PermissionError) as e:
                log.debug(f"Could not access {disk_conf['path']}: {e}")

        for dev, io_data in disk_io.items():
            conn.execute(
                """INSERT INTO disk_io
                   (timestamp, device, read_mb_s, write_mb_s, read_iops, write_iops, busy_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, dev, io_data["read_mb_s"], io_data["write_mb_s"],
                 io_data["read_iops"], io_data["write_iops"], io_data["busy_pct"])
            )

        if DOCKER_AVAILABLE:
            try:
                client = docker.from_env()
                for container in client.containers.list():
                    try:
                        stats = container.stats(stream=False)
                        cpu_pct = calculate_container_cpu(stats)
                        mem_usage = stats["memory_stats"].get("usage", 0)
                        mem_limit = stats["memory_stats"].get("limit", 1)
                        mem_mb = mem_usage / (1024**2)
                        mem_pct = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0

                        conn.execute(
                            "INSERT INTO container_metrics (timestamp, container_name, cpu_percent, ram_mb, ram_percent) VALUES (?, ?, ?, ?, ?)",
                            (ts, container.name, cpu_pct, mem_mb, mem_pct)
                        )
                    except Exception as e:
                        log.debug(f"Failed to get stats for {container.name}: {e}")
            except Exception as e:
                log.warning(f"Docker stats collection failed: {e}")

    log.debug(f"Collected metrics at {datetime.fromtimestamp(ts)}")

_drivetemp_map = None

def _resolve_drivetemp_label(sensor) -> str:
    global _drivetemp_map
    if _drivetemp_map is None:
        _drivetemp_map = {}
        try:
            import glob
            for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
                name_file = f"{hwmon}/name"
                if open(name_file).read().strip() == "drivetemp":
                    device_link = f"{hwmon}/device"
                    if os.path.islink(device_link):
                        dev = os.path.basename(os.readlink(device_link))
                        _drivetemp_map[hwmon] = dev
        except Exception:
            pass

    return sensor.label or "temp"

def collect_smart_data():
    if not shutil.which("smartctl"):
        log.debug("smartctl not available")
        return

    ts = time.time()

    try:
        result = subprocess.run(
            ["smartctl", "--scan", "-j"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return

        scan_data = json.loads(result.stdout)
        devices = scan_data.get("devices", [])
    except Exception as e:
        log.debug(f"SMART scan failed: {e}")
        return

    with get_db() as conn:
        for device in devices:
            dev_path = device.get("name", "")
            if not dev_path:
                continue

            try:
                result = subprocess.run(
                    ["smartctl", "-a", "-j", dev_path],
                    capture_output=True, text=True, timeout=60
                )

                smart = json.loads(result.stdout)

                model = smart.get("model_name", "Unknown")
                serial = smart.get("serial_number", "Unknown")
                health = smart.get("smart_status", {}).get("passed", None)
                health_str = "PASSED" if health else ("FAILED" if health is False else "UNKNOWN")

                temp = None
                power_hours = None

                if "temperature" in smart:
                    temp = smart["temperature"].get("current")

                if "power_on_time" in smart:
                    power_hours = smart["power_on_time"].get("hours")

                attrs = smart.get("ata_smart_attributes", {}).get("table", [])
                for attr in attrs:
                    if attr.get("name") == "Temperature_Celsius" and temp is None:
                        temp = attr.get("raw", {}).get("value")
                    if attr.get("name") == "Power_On_Hours" and power_hours is None:
                        power_hours = attr.get("raw", {}).get("value")

                nvme_health = smart.get("nvme_smart_health_information_log", {})
                if nvme_health:
                    if temp is None:
                        temp = nvme_health.get("temperature")
                    if power_hours is None:
                        power_hours = nvme_health.get("power_on_hours")

                important_attrs = {}
                for attr in attrs:
                    name = attr.get("name", "")
                    if any(x in name.lower() for x in ["reallocated", "pending", "uncorrectable", "wear", "error"]):
                        important_attrs[name] = attr.get("raw", {}).get("value")

                if nvme_health:
                    important_attrs["percentage_used"] = nvme_health.get("percentage_used")
                    important_attrs["available_spare"] = nvme_health.get("available_spare")
                    important_attrs["media_errors"] = nvme_health.get("media_errors")

                conn.execute(
                    """INSERT INTO disk_health
                       (timestamp, device, model, serial, temperature, power_on_hours, health_status, smart_data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, dev_path, model, serial, temp, power_hours, health_str, json.dumps(important_attrs))
                )

            except Exception as e:
                log.debug(f"Failed to get SMART for {dev_path}: {e}")

def compute_hourly_baseline():
    now = time.time()
    hour_bucket = int(now // 3600) * 3600
    hour_start = hour_bucket - 3600

    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM hourly_baselines WHERE hour_bucket = ? LIMIT 1",
            (hour_bucket,)
        ).fetchone()
        if existing:
            return

        row = conn.execute(
            """SELECT AVG(cpu_percent) as cpu, AVG(ram_percent) as ram,
                      AVG(iowait) as iowait, AVG(swap_used_gb) as swap
               FROM system_metrics WHERE timestamp >= ? AND timestamp < ?""",
            (hour_start, hour_bucket)
        ).fetchone()

        if row and row["cpu"] is not None:
            for metric, value in [("cpu_percent", row["cpu"]), ("ram_percent", row["ram"]),
                                  ("iowait", row["iowait"]), ("swap_used_gb", row["swap"])]:
                if value is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO hourly_baselines (hour_bucket, metric, value) VALUES (?, ?, ?)",
                        (hour_bucket, metric, value)
                    )

        temp_row = conn.execute(
            "SELECT AVG(temp) as avg_temp, MAX(temp) as max_temp FROM cpu_temps WHERE timestamp >= ? AND timestamp < ?",
            (hour_start, hour_bucket)
        ).fetchone()

        if temp_row and temp_row["avg_temp"] is not None:
            conn.execute(
                "INSERT OR REPLACE INTO hourly_baselines (hour_bucket, metric, value) VALUES (?, ?, ?)",
                (hour_bucket, "cpu_temp", temp_row["avg_temp"])
            )

        health_rows = conn.execute(
            """SELECT device, temperature FROM disk_health
               WHERE id IN (SELECT MAX(id) FROM disk_health GROUP BY device)"""
        ).fetchall()

        for h in health_rows:
            if h["temperature"] is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO hourly_baselines (hour_bucket, metric, value) VALUES (?, ?, ?)",
                    (hour_bucket, f"hdd_temp_{h['device']}", h["temperature"])
                )

    log.debug(f"Computed baselines for hour {datetime.fromtimestamp(hour_bucket)}")

def get_baselines() -> dict:
    cutoff = time.time() - (config.BASELINE_RETENTION_DAYS * 86400)

    with get_db() as conn:
        rows = conn.execute(
            """SELECT metric, AVG(value) as mean, COUNT(*) as cnt,
                      SUM((value - sub.avg_val) * (value - sub.avg_val)) as sum_sq
               FROM hourly_baselines
               JOIN (SELECT metric as m, AVG(value) as avg_val FROM hourly_baselines
                     WHERE hour_bucket > ? GROUP BY metric) sub ON sub.m = hourly_baselines.metric
               WHERE hour_bucket > ?
               GROUP BY metric""",
            (cutoff, cutoff)
        ).fetchall()

        result = {}
        for row in rows:
            cnt = row["cnt"]
            mean = row["mean"]
            stddev = math.sqrt(row["sum_sq"] / cnt) if cnt > 1 and row["sum_sq"] else 0

            result[row["metric"]] = {
                "mean": mean,
                "stddev": stddev,
                "count": cnt,
                "threshold": mean + (config.ANOMALY_SIGMA * stddev) if stddev > 0 else mean * 1.5,
            }

        return result

def calculate_container_cpu(stats: dict) -> float:
    try:
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                    stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                       stats["precpu_stats"]["system_cpu_usage"]
        cpu_count = stats["cpu_stats"]["online_cpus"]

        if system_delta > 0 and cpu_delta > 0:
            return (cpu_delta / system_delta) * cpu_count * 100
    except (KeyError, ZeroDivisionError):
        pass
    return 0.0

def get_metrics_since(seconds: int) -> dict:
    cutoff = time.time() - seconds

    with get_db() as conn:
        system = conn.execute(
            """SELECT AVG(cpu_percent) as cpu_avg, AVG(ram_percent) as ram_avg,
                      AVG(iowait) as iowait_avg, AVG(swap_used_gb) as swap_avg
               FROM system_metrics WHERE timestamp > ?""",
            (cutoff,)
        ).fetchone()

        cpu_temps = conn.execute(
            "SELECT core_id, AVG(temp) as temp_avg FROM cpu_temps WHERE timestamp > ? GROUP BY core_id",
            (cutoff,)
        ).fetchall()

        system_temps = conn.execute(
            "SELECT sensor_name, AVG(temp) as temp_avg FROM system_temps WHERE timestamp > ? GROUP BY sensor_name",
            (cutoff,)
        ).fetchall()

        disks = conn.execute(
            "SELECT disk_name, mount_path, AVG(percent_used) as pct, AVG(used_gb) as used, AVG(total_gb) as total FROM disk_metrics WHERE timestamp > ? GROUP BY disk_name",
            (cutoff,)
        ).fetchall()

        disk_health = conn.execute(
            """SELECT device, model, temperature, health_status
               FROM disk_health
               WHERE id IN (SELECT MAX(id) FROM disk_health GROUP BY device)""",
        ).fetchall()

        return {
            "cpu_percent": system["cpu_avg"] if system else None,
            "ram_percent": system["ram_avg"] if system else None,
            "iowait": system["iowait_avg"] if system else None,
            "swap_used_gb": system["swap_avg"] if system else None,
            "cpu_temps": {row["core_id"]: row["temp_avg"] for row in cpu_temps},
            "system_temps": {row["sensor_name"]: row["temp_avg"] for row in system_temps},
            "disks": [dict(row) for row in disks],
            "hdd_temps": {row["device"]: row["temperature"] for row in disk_health if row["temperature"]},
            "smart_status": {row["device"]: row["health_status"] for row in disk_health},
        }

def get_metrics_range(hours: int = 24) -> dict:
    cutoff = time.time() - (hours * 3600)

    with get_db() as conn:
        system = conn.execute(
            "SELECT timestamp, cpu_percent, ram_percent, ram_used_gb, iowait, swap_used_gb FROM system_metrics WHERE timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()

        cpu_temps = conn.execute(
            "SELECT timestamp, core_id, temp FROM cpu_temps WHERE timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()

        system_temps = conn.execute(
            "SELECT timestamp, sensor_name, temp FROM system_temps WHERE timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()

        containers = conn.execute(
            """SELECT container_name,
                      AVG(cpu_percent) as cpu_avg,
                      AVG(ram_mb) as ram_avg,
                      MAX(cpu_percent) as cpu_max,
                      MAX(ram_mb) as ram_max
               FROM container_metrics
               WHERE timestamp > ?
               GROUP BY container_name
               ORDER BY cpu_avg DESC""",
            (cutoff,)
        ).fetchall()

        top_container = None
        if containers:
            top_name = containers[0]["container_name"]
            top_container = conn.execute(
                "SELECT timestamp, cpu_percent, ram_mb FROM container_metrics WHERE timestamp > ? AND container_name = ? ORDER BY timestamp",
                (cutoff, top_name)
            ).fetchall()

        disks = conn.execute(
            """SELECT disk_name, mount_path,
                      AVG(percent_used) as pct_avg,
                      MAX(percent_used) as pct_max,
                      AVG(used_gb) as used_avg,
                      AVG(total_gb) as total_avg
               FROM disk_metrics WHERE timestamp > ? GROUP BY disk_name""",
            (cutoff,)
        ).fetchall()

        disk_health = conn.execute(
            """SELECT device, model, serial, temperature, power_on_hours, health_status, smart_data
               FROM disk_health
               WHERE id IN (SELECT MAX(id) FROM disk_health GROUP BY device)""",
        ).fetchall()

        disk_io = conn.execute(
            """SELECT device,
                      AVG(read_mb_s) as read_avg, MAX(read_mb_s) as read_max,
                      AVG(write_mb_s) as write_avg, MAX(write_mb_s) as write_max,
                      AVG(busy_pct) as busy_avg, MAX(busy_pct) as busy_max
               FROM disk_io WHERE timestamp > ? GROUP BY device""",
            (cutoff,)
        ).fetchall()

        disk_io_timeseries = conn.execute(
            "SELECT timestamp, device, read_mb_s, write_mb_s, busy_pct FROM disk_io WHERE timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()

        return {
            "system": [dict(row) for row in system],
            "cpu_temps": [dict(row) for row in cpu_temps],
            "system_temps": [dict(row) for row in system_temps],
            "containers": [dict(row) for row in containers],
            "top_container_name": containers[0]["container_name"] if containers else None,
            "top_container_data": [dict(row) for row in top_container] if top_container else [],
            "disks": [dict(row) for row in disks],
            "disk_health": [dict(row) for row in disk_health],
            "disk_io": [dict(row) for row in disk_io],
            "disk_io_timeseries": [dict(row) for row in disk_io_timeseries],
        }

def prune_old_data():
    cutoff = time.time() - (config.RETENTION_HOURS * 3600)
    health_cutoff = time.time() - (7 * 24 * 3600)
    baseline_cutoff = time.time() - (config.BASELINE_RETENTION_DAYS * 86400)

    with get_db() as conn:
        for table in ["system_metrics", "cpu_temps", "system_temps", "container_metrics", "disk_metrics", "disk_io"]:
            conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM disk_health WHERE timestamp < ?", (health_cutoff,))
        conn.execute("DELETE FROM hourly_baselines WHERE hour_bucket < ?", (baseline_cutoff,))

    log.info(f"Pruned data older than {config.RETENTION_HOURS} hours, baselines older than {config.BASELINE_RETENTION_DAYS} days")
