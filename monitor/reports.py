import io
import json
import logging
from datetime import datetime
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests

import config
import metrics

log = logging.getLogger(__name__)

def fmt_size(gb: float, precision: int = 1) -> str:
    if gb >= 1000:
        return f"{gb / 1000:.{precision}f} TB"
    elif gb >= 1:
        return f"{gb:.{precision}f} GB"
    else:
        return f"{gb * 1024:.0f} MB"

def fmt_size_pair(used_gb: float, total_gb: float) -> str:
    if total_gb >= 1000:
        return f"{used_gb / 1000:.2f} / {total_gb / 1000:.1f} TB"
    else:
        return f"{used_gb:.0f} / {total_gb:.0f} GB"

def generate_report():
    data = metrics.get_metrics_range(hours=24)

    if not data["system"]:
        log.warning("No data available for report")
        return

    fig = create_charts(data)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=config.CHART_STYLE["bg_color"])
    buf.seek(0)
    plt.close(fig)

    stats = calculate_stats(data)
    send_report(buf, stats)

def create_charts(data: dict) -> plt.Figure:
    style = config.CHART_STYLE

    plt.rcParams.update({
        "figure.facecolor": style["bg_color"],
        "axes.facecolor": style["bg_color"],
        "axes.edgecolor": style["grid_color"],
        "axes.labelcolor": style["fg_color"],
        "text.color": style["fg_color"],
        "xtick.color": style["fg_color"],
        "ytick.color": style["fg_color"],
        "grid.color": style["grid_color"],
        "legend.facecolor": style["bg_color"],
        "legend.edgecolor": style["grid_color"],
    })

    fig, axes = plt.subplots(4, 2, figsize=(14, 18))
    fig.suptitle(f"System Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 fontsize=16, fontweight="bold", color=style["fg_color"])

    plot_cpu_usage(axes[0, 0], data["system"], style)
    plot_ram_swap(axes[0, 1], data["system"], style)
    plot_cpu_temps(axes[1, 0], data["cpu_temps"], style)
    plot_iowait(axes[1, 1], data["system"], style)
    plot_system_temps(axes[2, 0], data["system_temps"], style)
    plot_disk_io(axes[2, 1], data.get("disk_io_timeseries", []), style)
    plot_top_container(axes[3, 0], data, style)
    plot_disk_usage(axes[3, 1], data["disks"], style)

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    return fig

def plot_cpu_usage(ax, data: list, style: dict):
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("CPU Usage", fontweight="bold")
        return

    times = [datetime.fromtimestamp(d["timestamp"]) for d in data]
    values = [d["cpu_percent"] or 0 for d in data]

    ax.fill_between(times, values, alpha=0.3, color=style["accent_colors"][0])
    ax.plot(times, values, color=style["accent_colors"][0], linewidth=1.5)

    avg = sum(values) / len(values) if values else 0
    ax.axhline(y=avg, color=style["accent_colors"][0], linestyle=":", alpha=0.5)

    ax.set_title(f"CPU Usage (avg: {avg:.1f}%)", fontweight="bold")
    ax.set_ylabel("Usage %")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_ram_swap(ax, data: list, style: dict):
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("RAM & Swap", fontweight="bold")
        return

    times = [datetime.fromtimestamp(d["timestamp"]) for d in data]
    ram_pct = [d["ram_percent"] or 0 for d in data]
    swap_gb = [d.get("swap_used_gb") or 0 for d in data]

    ax.fill_between(times, ram_pct, alpha=0.3, color=style["accent_colors"][1])
    line1, = ax.plot(times, ram_pct, color=style["accent_colors"][1], linewidth=1.5, label="RAM %")
    ax.set_ylabel("RAM %", color=style["accent_colors"][1])
    ax.set_ylim(0, 100)

    ax2 = ax.twinx()
    line2, = ax2.plot(times, swap_gb, color=style["accent_colors"][3], linewidth=1.5, label="Swap GB", alpha=0.8)
    ax2.set_ylabel("Swap GB", color=style["accent_colors"][3])
    ax2.tick_params(axis="y", labelcolor=style["accent_colors"][3])

    avg_ram = sum(ram_pct) / len(ram_pct) if ram_pct else 0
    avg_swap = sum(swap_gb) / len(swap_gb) if swap_gb else 0

    ax.set_title(f"RAM (avg: {avg_ram:.1f}%) | Swap (avg: {avg_swap:.1f} GB)", fontweight="bold")
    ax.legend([line1, line2], ["RAM %", "Swap GB"], loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_cpu_temps(ax, data: list, style: dict):
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("CPU Temperature", fontweight="bold")
        return

    by_ts = defaultdict(list)
    for d in data:
        by_ts[d["timestamp"]].append(d["temp"])

    times = sorted(by_ts.keys())
    avg_values = [sum(by_ts[t]) / len(by_ts[t]) for t in times]
    max_values = [max(by_ts[t]) for t in times]
    times_dt = [datetime.fromtimestamp(t) for t in times]

    ax.fill_between(times_dt, avg_values, alpha=0.3, color=style["accent_colors"][2])
    ax.plot(times_dt, avg_values, color=style["accent_colors"][2], linewidth=1.5, label="Average")
    ax.plot(times_dt, max_values, color=style["accent_colors"][4], linewidth=1, alpha=0.6, label="Max Core")

    ax.axhline(y=config.TEMP_WARN_CPU, color=style["warn_color"], linestyle="--", alpha=0.5, linewidth=1)

    overall_avg = sum(avg_values) / len(avg_values) if avg_values else 0
    ax.set_title(f"CPU Temperature (avg: {overall_avg:.1f}\u00b0C)", fontweight="bold")
    ax.set_ylabel("Temperature \u00b0C")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_iowait(ax, data: list, style: dict):
    iowait_data = [(d["timestamp"], d.get("iowait") or 0) for d in data if d.get("iowait") is not None]

    if not iowait_data:
        ax.text(0.5, 0.5, "No I/O wait data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("I/O Wait", fontweight="bold")
        return

    times = [datetime.fromtimestamp(d[0]) for d in iowait_data]
    values = [d[1] for d in iowait_data]

    ax.fill_between(times, values, alpha=0.3, color=style["accent_colors"][1])
    ax.plot(times, values, color=style["accent_colors"][1], linewidth=1.5)

    ax.axhline(y=config.IOWAIT_WARN, color=style["warn_color"], linestyle="--", alpha=0.5, linewidth=1, label=f"Warn ({config.IOWAIT_WARN}%)")
    ax.axhline(y=config.IOWAIT_CRIT, color=style["crit_color"], linestyle="--", alpha=0.5, linewidth=1, label=f"Crit ({config.IOWAIT_CRIT}%)")

    avg = sum(values) / len(values) if values else 0
    peak = max(values) if values else 0
    ax.set_title(f"I/O Wait (avg: {avg:.1f}%, peak: {peak:.1f}%)", fontweight="bold")
    ax.set_ylabel("I/O Wait %")
    ax.set_ylim(0, max(100, peak * 1.1))
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_disk_io(ax, data: list, style: dict):
    if not data:
        ax.text(0.5, 0.5, "No disk I/O data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("Disk I/O", fontweight="bold")
        return

    by_device = defaultdict(list)
    for d in data:
        by_device[d["device"]].append(d)

    color_map = {"sda": style["accent_colors"][0], "sdb": style["accent_colors"][1], "sdc": style["accent_colors"][2]}

    for dev, points in sorted(by_device.items()):
        times = [datetime.fromtimestamp(p["timestamp"]) for p in points]
        busy = [p["busy_pct"] for p in points]
        color = color_map.get(dev, style["accent_colors"][3])
        ax.plot(times, busy, color=color, linewidth=1.2, label=dev, alpha=0.8)

    ax.set_title("Disk Busy %", fontweight="bold")
    ax.set_ylabel("Busy %")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_top_container(ax, data: dict, style: dict):
    container_name = data.get("top_container_name")
    container_data = data.get("top_container_data", [])

    if not container_data:
        ax.text(0.5, 0.5, "No container data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("Top Container", fontweight="bold")
        return

    times = [datetime.fromtimestamp(d["timestamp"]) for d in container_data]
    cpu = [d["cpu_percent"] or 0 for d in container_data]
    ram = [d["ram_mb"] or 0 for d in container_data]

    ax.fill_between(times, cpu, alpha=0.3, color=style["accent_colors"][0])
    line1, = ax.plot(times, cpu, color=style["accent_colors"][0], linewidth=1.5, label="CPU %")
    ax.set_ylabel("CPU %", color=style["accent_colors"][0])
    ax.tick_params(axis="y", labelcolor=style["accent_colors"][0])

    ax2 = ax.twinx()
    ax2.fill_between(times, ram, alpha=0.2, color=style["accent_colors"][1])
    line2, = ax2.plot(times, ram, color=style["accent_colors"][1], linewidth=1.5, label="RAM MB")
    ax2.set_ylabel("RAM MB", color=style["accent_colors"][1])
    ax2.tick_params(axis="y", labelcolor=style["accent_colors"][1])

    short_name = container_name[:25] + "..." if len(container_name) > 25 else container_name
    ax.set_title(f"Top Container: {short_name}", fontweight="bold")
    ax.legend([line1, line2], ["CPU %", "RAM MB"], loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_system_temps(ax, data: list, style: dict):
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("System Temperatures", fontweight="bold")
        return

    by_sensor = defaultdict(list)
    for d in data:
        by_sensor[d["sensor_name"]].append((d["timestamp"], d["temp"]))

    for i, (sensor, points) in enumerate(sorted(by_sensor.items())):
        times = [datetime.fromtimestamp(p[0]) for p in points]
        temps = [p[1] for p in points]
        color = style["accent_colors"][i % len(style["accent_colors"])]

        label = sensor.split(":")[-1] if ":" in sensor else sensor
        label = label[:12]
        ax.plot(times, temps, color=color, linewidth=1.5, label=label, alpha=0.8)

    ax.axhline(y=config.TEMP_WARN_SYSTEM, color=style["warn_color"], linestyle="--", alpha=0.5, linewidth=1)

    ax.set_title("System Temperatures", fontweight="bold")
    ax.set_ylabel("Temperature \u00b0C")
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

def plot_disk_usage(ax, data: list, style: dict):
    if not data:
        ax.text(0.5, 0.5, "No disk data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("Disk Usage", fontweight="bold")
        return

    names = [d["disk_name"] for d in data]
    used = [d["used_avg"] or 0 for d in data]
    total = [d["total_avg"] or 1 for d in data]
    pcts = [d["pct_avg"] or 0 for d in data]

    y_pos = range(len(names))

    colors = []
    for pct in pcts:
        if pct >= config.DISK_CRIT:
            colors.append(style["crit_color"])
        elif pct >= config.DISK_WARN:
            colors.append(style["warn_color"])
        else:
            colors.append(style["accent_colors"][2])

    ax.barh(y_pos, total, color=style["grid_color"], height=0.6, label="Total")
    ax.barh(y_pos, used, color=colors, height=0.6, label="Used")

    for i, (u, t, p) in enumerate(zip(used, total, pcts)):
        label = f"{fmt_size_pair(u, t)} ({p:.0f}%)"
        ax.text(t + max(total) * 0.02, i, label, va="center", fontsize=9, color=style["fg_color"])

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names)
    ax.set_xlabel("GB")
    ax.set_title("Disk Usage", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="x")

    max_val = max(total) if total else 100
    ax.set_xlim(0, max_val * 1.3)

def calculate_stats(data: dict) -> dict:
    stats = {
        "cpu_avg": 0, "cpu_max": 0,
        "ram_avg": 0, "ram_max": 0,
        "ram_used_avg": 0, "ram_total": 0,
        "temp_avg": 0, "temp_max": 0,
        "iowait_avg": 0, "iowait_max": 0,
        "swap_avg": 0, "swap_max": 0,
        "containers": [], "disks": [], "disk_health": [], "disk_io": [],
    }

    if data["system"]:
        cpu_values = [d["cpu_percent"] for d in data["system"] if d["cpu_percent"]]
        ram_values = [d["ram_percent"] for d in data["system"] if d["ram_percent"]]
        ram_used = [d["ram_used_gb"] for d in data["system"] if d["ram_used_gb"]]
        iowait_values = [d.get("iowait") or 0 for d in data["system"] if d.get("iowait") is not None]
        swap_values = [d.get("swap_used_gb") or 0 for d in data["system"]]

        if cpu_values:
            stats["cpu_avg"] = sum(cpu_values) / len(cpu_values)
            stats["cpu_max"] = max(cpu_values)
        if ram_values:
            stats["ram_avg"] = sum(ram_values) / len(ram_values)
            stats["ram_max"] = max(ram_values)
        if ram_used:
            stats["ram_used_avg"] = sum(ram_used) / len(ram_used)
            last = data["system"][-1]
            if last["ram_percent"] and last["ram_percent"] > 0:
                stats["ram_total"] = last["ram_used_gb"] / (last["ram_percent"] / 100)
        if iowait_values:
            stats["iowait_avg"] = sum(iowait_values) / len(iowait_values)
            stats["iowait_max"] = max(iowait_values)
        if swap_values:
            stats["swap_avg"] = sum(swap_values) / len(swap_values)
            stats["swap_max"] = max(swap_values)

    if data["cpu_temps"]:
        temps = [d["temp"] for d in data["cpu_temps"]]
        stats["temp_avg"] = sum(temps) / len(temps)
        stats["temp_max"] = max(temps)

    if data["containers"]:
        stats["containers"] = data["containers"][:10]

    if data["disks"]:
        stats["disks"] = data["disks"]

    if data["disk_health"]:
        stats["disk_health"] = data["disk_health"]

    if data.get("disk_io"):
        stats["disk_io"] = data["disk_io"]

    return stats

def send_report(image_buf: io.BytesIO, stats: dict):
    if not config.DISCORD_WEBHOOK:
        log.warning("No Discord webhook configured")
        return

    container_text = ""
    if stats["containers"]:
        lines = []
        for c in stats["containers"][:6]:
            name = c["container_name"][:18]
            ram_gb = c["ram_avg"] / 1024
            ram_str = fmt_size(ram_gb)
            lines.append(f"`{name:18}` CPU {c['cpu_avg']:5.1f}% | RAM {ram_str}")
        container_text = "\n".join(lines)

    disk_text = ""
    if stats["disks"]:
        lines = []
        for d in stats["disks"]:
            name = d["disk_name"][:12]
            used = d["used_avg"] or 0
            total = d["total_avg"] or 0
            pct = d["pct_avg"] or 0
            size_str = fmt_size_pair(used, total)
            lines.append(f"`{name:12}` {size_str} ({pct:.0f}%)")
        disk_text = "\n".join(lines)

    health_text = ""
    if stats["disk_health"]:
        lines = []
        for h in stats["disk_health"]:
            model = (h["model"] or "Unknown")[:20]
            status = h["health_status"] or "?"
            temp = h["temperature"]
            temp_str = f"{temp}\u00b0C" if temp else "N/A"
            hours = h["power_on_hours"]
            hours_str = f"{hours:,}h" if hours else "N/A"

            smart_data = json.loads(h["smart_data"]) if h["smart_data"] else {}
            issues = []
            for k, v in smart_data.items():
                if v and v > 0 and "error" in k.lower():
                    issues.append(f"{k}: {v}")

            status_icon = "PASS" if status == "PASSED" else "FAIL" if status == "FAILED" else "?"
            line = f"`[{status_icon}]` `{model}` {temp_str} | {hours_str}"
            if issues:
                line += f" | {', '.join(issues[:2])}"
            lines.append(line)
        health_text = "\n".join(lines)

    io_text = ""
    if stats.get("disk_io"):
        lines = []
        for d in stats["disk_io"]:
            dev = d["device"]
            r_avg = d.get("read_avg", 0) or 0
            w_avg = d.get("write_avg", 0) or 0
            busy = d.get("busy_avg", 0) or 0
            lines.append(f"`{dev}` R: {r_avg:.0f} MB/s | W: {w_avg:.0f} MB/s | Busy: {busy:.0f}%")
        io_text = "\n".join(lines)

    embed = {
        "title": "Daily System Report",
        "color": 0x00D4FF,
        "timestamp": datetime.utcnow().isoformat(),
        "fields": [
            {
                "name": "CPU",
                "value": f"Avg: **{stats['cpu_avg']:.1f}%**\nMax: {stats['cpu_max']:.1f}%",
                "inline": True,
            },
            {
                "name": "RAM",
                "value": f"Avg: **{fmt_size(stats['ram_used_avg'])}** / {fmt_size(stats['ram_total'])}\nMax: {stats['ram_max']:.1f}%",
                "inline": True,
            },
            {
                "name": "CPU Temp",
                "value": f"Avg: **{stats['temp_avg']:.1f}\u00b0C**\nMax: {stats['temp_max']:.1f}\u00b0C",
                "inline": True,
            },
            {
                "name": "I/O Wait",
                "value": f"Avg: **{stats['iowait_avg']:.1f}%**\nMax: {stats['iowait_max']:.1f}%",
                "inline": True,
            },
            {
                "name": "Swap",
                "value": f"Avg: **{stats['swap_avg']:.1f} GB**\nMax: {stats['swap_max']:.1f} GB",
                "inline": True,
            },
        ],
        "image": {"url": "attachment://report.png"},
        "footer": {"text": "24-hour statistics"},
    }

    if disk_text:
        embed["fields"].append({"name": "Storage", "value": disk_text, "inline": False})

    if io_text:
        embed["fields"].append({"name": "Disk I/O (24h avg)", "value": io_text, "inline": False})

    if container_text:
        embed["fields"].append({"name": "Top Containers", "value": container_text, "inline": False})

    if health_text:
        embed["fields"].append({"name": "Disk Health (SMART)", "value": health_text, "inline": False})

    try:
        resp = requests.post(
            config.DISCORD_WEBHOOK,
            data={"payload_json": json.dumps({"embeds": [embed]})},
            files={"file": ("report.png", image_buf, "image/png")},
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Sent daily report")
    except Exception as e:
        log.error(f"Failed to send report: {e}")
