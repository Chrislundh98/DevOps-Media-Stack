#!/usr/bin/env python3
import argparse
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")

import metrics
import alerts
import reports
import config

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

def main():
    parser = argparse.ArgumentParser(description="System Monitor CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize database")
    sub.add_parser("collect", help="Run one collection cycle")
    sub.add_parser("smart", help="Collect SMART data")
    sub.add_parser("check", help="Run alert check")
    sub.add_parser("report", help="Generate and send daily report")
    sub.add_parser("status", help="Show current metrics")
    sub.add_parser("disks", help="Show disk status")
    sub.add_parser("baselines", help="Show learned baselines")
    sub.add_parser("prune", help="Prune old data")

    args = parser.parse_args()

    if args.cmd == "init":
        metrics.init_db()
        print(f"Database initialized at {config.DB_PATH}")

    elif args.cmd == "collect":
        metrics.init_db()
        metrics.collect_metrics()
        print("Collection complete")

    elif args.cmd == "smart":
        metrics.init_db()
        metrics.collect_smart_data()
        print("SMART collection complete")

    elif args.cmd == "check":
        metrics.init_db()
        alerts.check_alerts()
        print("Alert check complete")

    elif args.cmd == "report":
        metrics.init_db()
        reports.generate_report()
        print("Report sent")

    elif args.cmd == "status":
        metrics.init_db()
        data = metrics.get_metrics_since(300)

        print("\n" + "=" * 50)
        print("  CURRENT STATUS (5-min avg)")
        print("=" * 50)

        print(f"\nCPU Usage:  {data['cpu_percent']:.1f}%" if data['cpu_percent'] else "\nCPU Usage:  N/A")
        print(f"RAM Usage:  {data['ram_percent']:.1f}%" if data['ram_percent'] else "RAM Usage:  N/A")

        if data.get('iowait') is not None:
            print(f"I/O Wait:   {data['iowait']:.1f}%")
        if data.get('swap_used_gb') is not None:
            print(f"Swap Used:  {data['swap_used_gb']:.1f} GB")

        if data['cpu_temps']:
            avg = sum(data['cpu_temps'].values()) / len(data['cpu_temps'])
            print(f"\nCPU Temp (avg): {avg:.1f}\u00b0C")
            for core, temp in sorted(data['cpu_temps'].items()):
                print(f"  {core}: {temp:.1f}\u00b0C")

        if data['system_temps']:
            print("\nSystem Temps:")
            for sensor, temp in sorted(data['system_temps'].items()):
                print(f"  {sensor}: {temp:.1f}\u00b0C")

        if data.get('hdd_temps'):
            print("\nHDD Temps (SMART):")
            for dev, temp in sorted(data['hdd_temps'].items()):
                print(f"  {dev}: {temp:.0f}\u00b0C")

        if data['disks']:
            print("\nDisk Usage:")
            for disk in data['disks']:
                name = disk['disk_name']
                pct = disk['pct'] or 0
                used = disk['used'] or 0
                total = disk['total'] or 0
                print(f"  {name}: {fmt_size_pair(used, total)} ({pct:.1f}%)")

    elif args.cmd == "disks":
        metrics.init_db()
        data = metrics.get_metrics_range(hours=1)

        print("\n" + "=" * 50)
        print("  DISK STATUS")
        print("=" * 50)

        if data['disks']:
            print("\nUsage:")
            for disk in data['disks']:
                name = disk['disk_name']
                pct = disk['pct_avg'] or 0
                used = disk['used_avg'] or 0
                total = disk['total_avg'] or 0
                bar_len = int(pct / 5)
                bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
                size_str = fmt_size_pair(used, total)
                print(f"  {name:12} [{bar}] {pct:5.1f}% ({size_str})")

        if data['disk_health']:
            print("\nSMART Health:")
            for h in data['disk_health']:
                model = (h['model'] or 'Unknown')[:25]
                status = h['health_status'] or '?'
                temp = h['temperature']
                hours = h['power_on_hours']

                status_icon = "OK" if status == "PASSED" else "!!" if status == "FAILED" else "??"
                temp_str = f"{temp}\u00b0C" if temp else "N/A"
                hours_str = f"{hours:,}h" if hours else "N/A"

                print(f"  [{status_icon}] {model:25} | Temp: {temp_str:6} | Power-on: {hours_str}")

                if h['smart_data']:
                    smart = json.loads(h['smart_data'])
                    important = {k: v for k, v in smart.items() if v and v > 0}
                    if important:
                        for k, v in important.items():
                            print(f"      {k}: {v}")

    elif args.cmd == "baselines":
        metrics.init_db()
        baselines = metrics.get_baselines()

        print("\n" + "=" * 50)
        print("  LEARNED BASELINES")
        print("=" * 50)

        if not baselines:
            print("\n  No baselines yet. System is still learning.")
            print(f"  Need {config.ANOMALY_MIN_BASELINES} hourly samples (~2 days).")
        else:
            total = max(b["count"] for b in baselines.values()) if baselines else 0
            ready = total >= config.ANOMALY_MIN_BASELINES
            mode = "ADAPTIVE" if ready else "LEARNING"
            print(f"\n  Mode: {mode} ({total}/{config.ANOMALY_MIN_BASELINES} samples)")
            print(f"  Anomaly sigma: {config.ANOMALY_SIGMA} std devs\n")

            print(f"  {'Metric':<20} {'Mean':>8} {'StdDev':>8} {'Threshold':>10} {'Samples':>8}")
            print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*10} {'-'*8}")

            for name, bl in sorted(baselines.items()):
                unit = "\u00b0C" if "temp" in name else "%" if "percent" in name or name == "iowait" else " GB"
                print(f"  {name:<20} {bl['mean']:>7.1f}{unit} {bl['stddev']:>7.1f} {bl['threshold']:>9.1f}{unit} {bl['count']:>7}")

    elif args.cmd == "prune":
        metrics.init_db()
        metrics.prune_old_data()
        print("Prune complete")

if __name__ == "__main__":
    main()
