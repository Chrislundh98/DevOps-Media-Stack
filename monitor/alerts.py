import time
import logging
from datetime import datetime
from dataclasses import dataclass, field

import requests

import config
import metrics

log = logging.getLogger(__name__)

@dataclass
class AnomalyEntry:
    metric: str
    current: float
    baseline_mean: float
    baseline_threshold: float
    detail: str

class AlertManager:
    def __init__(self):
        self.last_digest_time: float = 0
        self.last_critical_times: dict[str, float] = {}
        self.pending_anomalies: list[AnomalyEntry] = []
        self._critical_cooldown = 3600  # 1 hour between repeat criticals

    def check_alerts(self):
        data = metrics.get_metrics_since(config.ANOMALY_WINDOW)
        baselines = metrics.get_baselines()

        self._check_criticals(data)
        self._check_anomalies(data, baselines)
        self._maybe_send_digest()

    def _check_criticals(self, data: dict):
        now = time.time()

        if data["cpu_temps"]:
            avg_temp = sum(data["cpu_temps"].values()) / len(data["cpu_temps"])
            if avg_temp >= config.CRIT_CPU_TEMP:
                self._send_critical("cpu_temp_crit",
                    "CPU Temperature Critical",
                    f"CPU at **{avg_temp:.0f}\u00b0C** \u2014 thermal throttling imminent.\n"
                    f"Check case airflow and CPU cooler immediately.")

        for device, temp in data.get("hdd_temps", {}).items():
            if temp is None:
                continue

            is_nvme = "nvme" in device.lower()
            crit_limit = config.CRIT_NVME_TEMP if is_nvme else config.CRIT_HDD_TEMP
            device_type = "NVMe" if is_nvme else "HDD"

            if temp >= crit_limit:
                self._send_critical(f"drive_crit_{device}",
                    f"{device_type} Temperature Critical",
                    f"**{device}** at **{temp:.0f}\u00b0C** \u2014 exceeds rated max ({crit_limit}\u00b0C).\n"
                    f"Risk of {'thermal throttling' if is_nvme else 'data loss'}. Consider shutting down non-essential I/O.")

        for device, status in data.get("smart_status", {}).items():
            if status == "FAILED":
                self._send_critical(f"smart_fail_{device}",
                    "SMART Health FAILED",
                    f"**{device}** reports SMART failure.\n"
                    f"**Back up data immediately.** Disk failure may be imminent.\n"
                    f"```\nsudo smartctl -a {device}\n```")

        for disk in data.get("disks", []):
            pct = disk.get("pct")
            if pct is not None and pct >= config.CRIT_DISK_PCT:
                self._send_critical(f"disk_crit_{disk['disk_name']}",
                    "Disk Space Critical",
                    f"**{disk['disk_name']}** at **{pct:.1f}%** \u2014 writes may fail.\n"
                    f"```\ndu -sh /mnt/storage/data/downloads/*/ | sort -rh | head -20\n```")

    def _check_anomalies(self, data: dict, baselines: dict):
        adaptive = self._has_enough_baselines(baselines)

        # Each check: (metric_name, current_value, fallback_threshold, floor, detail_fn)
        # The floor is the absolute minimum - values below it are NEVER anomalies,
        # regardless of what the adaptive baseline says.
        checks = [
            ("cpu_percent", data.get("cpu_percent"), config.FALLBACK_CPU_PCT, config.FLOOR_CPU_PCT,
             lambda v, b: f"CPU usage at **{v:.1f}%** (baseline: {b['mean']:.1f}% \u00b1 {b['stddev']:.1f})" if b else f"CPU usage at **{v:.1f}%**"),

            ("ram_percent", data.get("ram_percent"), config.FALLBACK_RAM_PCT, config.FLOOR_RAM_PCT,
             lambda v, b: f"RAM at **{v:.1f}%** (baseline: {b['mean']:.1f}%)" if b else f"RAM at **{v:.1f}%**"),

            ("iowait", data.get("iowait"), config.FALLBACK_IOWAIT, config.FLOOR_IOWAIT,
             lambda v, b: (
                 f"I/O wait at **{v:.1f}%** (baseline: {b['mean']:.1f}% \u00b1 {b['stddev']:.1f})\n"
                 f"Streaming may be affected. Top I/O consumers:\n"
                 f"```\nsudo iotop -oPa -d5 -n2 | head -15\n```"
             ) if b else f"I/O wait at **{v:.1f}%**"),

            ("swap_used_gb", data.get("swap_used_gb"), config.FALLBACK_SWAP_GB, config.FLOOR_SWAP_GB,
             lambda v, b: (
                 f"Swap usage at **{v:.1f} GB** (baseline: {b['mean']:.1f} GB)\n"
                 f"To reclaim:\n"
                 f"```\nfree -g  # check free RAM first\n"
                 f"sudo swapoff -a && sudo swapon -a\n```"
             ) if b else (
                 f"Swap usage at **{v:.1f} GB**\n"
                 f"To reclaim:\n"
                 f"```\nfree -g  # check free RAM first\n"
                 f"sudo swapoff -a && sudo swapon -a\n```"
             )),
        ]

        for metric_name, current_value, fallback, floor, detail_fn in checks:
            if current_value is None:
                continue

            # Floor gate: value below absolute floor is healthy, skip entirely
            if current_value < floor:
                continue

            baseline = baselines.get(metric_name)

            if adaptive and baseline and baseline["count"] >= config.ANOMALY_MIN_BASELINES:
                # Effective threshold is the higher of baseline-derived and floor
                threshold = max(baseline["threshold"], floor)
                if current_value > threshold:
                    self.pending_anomalies.append(AnomalyEntry(
                        metric=metric_name,
                        current=current_value,
                        baseline_mean=baseline["mean"],
                        baseline_threshold=threshold,
                        detail=detail_fn(current_value, baseline),
                    ))
            else:
                if current_value > fallback:
                    self.pending_anomalies.append(AnomalyEntry(
                        metric=metric_name,
                        current=current_value,
                        baseline_mean=fallback,
                        baseline_threshold=fallback,
                        detail=detail_fn(current_value, baseline),
                    ))

        # CPU temperature with floor gate
        if data.get("cpu_temps"):
            avg_temp = sum(data["cpu_temps"].values()) / len(data["cpu_temps"])

            # Below floor = healthy, don't even check baseline
            if avg_temp >= config.FLOOR_CPU_TEMP:
                baseline = baselines.get("cpu_temp")

                if adaptive and baseline and baseline["count"] >= config.ANOMALY_MIN_BASELINES:
                    threshold = max(baseline["threshold"], config.FLOOR_CPU_TEMP)
                    if avg_temp > threshold:
                        self.pending_anomalies.append(AnomalyEntry(
                            metric="cpu_temp",
                            current=avg_temp,
                            baseline_mean=baseline["mean"],
                            baseline_threshold=threshold,
                            detail=f"CPU temp at **{avg_temp:.1f}\u00b0C** (baseline: {baseline['mean']:.1f}\u00b0C \u00b1 {baseline['stddev']:.1f})",
                        ))
                elif avg_temp > config.FALLBACK_CPU_TEMP:
                    self.pending_anomalies.append(AnomalyEntry(
                        metric="cpu_temp",
                        current=avg_temp,
                        baseline_mean=config.FALLBACK_CPU_TEMP,
                        baseline_threshold=config.FALLBACK_CPU_TEMP,
                        detail=f"CPU temp at **{avg_temp:.1f}\u00b0C**",
                    ))

        # Drive temperatures with floor gate
        for device, temp in data.get("hdd_temps", {}).items():
            if temp is None:
                continue

            is_nvme = "nvme" in device.lower()
            crit_limit = config.CRIT_NVME_TEMP if is_nvme else config.CRIT_HDD_TEMP
            floor = config.FLOOR_NVME_TEMP if is_nvme else config.FLOOR_HDD_TEMP

            # Already handled by criticals
            if temp >= crit_limit:
                continue

            # Below floor = healthy
            if temp < floor:
                continue

            bl_key = f"hdd_temp_{device}"
            baseline = baselines.get(bl_key)
            fallback = 75 if is_nvme else config.FALLBACK_HDD_TEMP

            if adaptive and baseline and baseline["count"] >= config.ANOMALY_MIN_BASELINES:
                threshold = max(baseline["threshold"], floor)
                if temp > threshold:
                    self.pending_anomalies.append(AnomalyEntry(
                        metric=bl_key,
                        current=temp,
                        baseline_mean=baseline["mean"],
                        baseline_threshold=baseline["threshold"],
                        detail=f"{device} at **{temp:.0f}\u00b0C** (baseline: {baseline['mean']:.1f}\u00b0C \u00b1 {baseline['stddev']:.1f})",
                    ))
            elif temp > fallback:
                self.pending_anomalies.append(AnomalyEntry(
                    metric=bl_key,
                    current=temp,
                    baseline_mean=fallback,
                    baseline_threshold=fallback,
                    detail=f"{device} at **{temp:.0f}\u00b0C**",
                ))

    def _maybe_send_digest(self):
        if not self.pending_anomalies:
            return

        now = time.time()
        if now - self.last_digest_time < config.DIGEST_INTERVAL_HOURS * 3600:
            return

        seen_metrics = set()
        unique_anomalies = []
        for a in self.pending_anomalies:
            if a.metric not in seen_metrics:
                seen_metrics.add(a.metric)
                unique_anomalies.append(a)

        if not unique_anomalies:
            self.pending_anomalies.clear()
            return

        self._send_digest(unique_anomalies)
        self.pending_anomalies.clear()
        self.last_digest_time = now

    def _has_enough_baselines(self, baselines: dict) -> bool:
        for bl in baselines.values():
            if bl["count"] >= config.ANOMALY_MIN_BASELINES:
                return True
        return False

    def _send_critical(self, key: str, title: str, message: str):
        now = time.time()
        if now - self.last_critical_times.get(key, 0) < self._critical_cooldown:
            return

        self.last_critical_times[key] = now

        if not config.DISCORD_WEBHOOK:
            return

        embed = {
            "title": title,
            "description": message,
            "color": 0xFF4444,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Critical alert \u2014 immediate attention required"},
        }

        try:
            resp = requests.post(config.DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
            resp.raise_for_status()
            log.info(f"Sent critical alert: {title}")
        except Exception as e:
            log.error(f"Failed to send critical alert: {e}")

    def _send_digest(self, anomalies: list[AnomalyEntry]):
        if not config.DISCORD_WEBHOOK:
            return

        lines = []
        for a in anomalies:
            lines.append(a.detail)

        baselines = metrics.get_baselines()
        any_adaptive = any(
            baselines.get(a.metric, {}).get("count", 0) >= config.ANOMALY_MIN_BASELINES
            for a in anomalies
        )

        description = "\n\n".join(lines)

        mode = "adaptive baseline" if any_adaptive else f"static fallbacks (learning, need {config.ANOMALY_MIN_BASELINES}h of data)"

        embed = {
            "title": f"System Digest \u2014 {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}",
            "description": description,
            "color": 0xFFAA00,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": f"Detection: {mode} | Next digest in {config.DIGEST_INTERVAL_HOURS}h"},
        }

        try:
            resp = requests.post(config.DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
            resp.raise_for_status()
            log.info(f"Sent anomaly digest with {len(anomalies)} entries")
        except Exception as e:
            log.error(f"Failed to send digest: {e}")

alert_manager = AlertManager()

def check_alerts():
    return alert_manager.check_alerts()
