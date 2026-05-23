import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
import metrics
import alerts
import reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("monitor")

def collect_job():
    try:
        metrics.collect_metrics()
    except Exception as e:
        log.error(f"Collection failed: {e}")

def alert_job():
    try:
        alerts.check_alerts()
    except Exception as e:
        log.error(f"Alert check failed: {e}")

def report_job():
    try:
        log.info("Generating daily report...")
        reports.generate_report()
    except Exception as e:
        log.error(f"Report generation failed: {e}")

def prune_job():
    try:
        metrics.prune_old_data()
    except Exception as e:
        log.error(f"Prune failed: {e}")

def smart_job():
    try:
        metrics.collect_smart_data()
        log.debug("SMART data collected")
    except Exception as e:
        log.error(f"SMART collection failed: {e}")

def baseline_job():
    try:
        metrics.compute_hourly_baseline()
    except Exception as e:
        log.error(f"Baseline computation failed: {e}")

def main():
    log.info("=" * 50)
    log.info("System Monitor Starting")
    log.info(f"  Collection interval: {config.COLLECT_INTERVAL}s")
    log.info(f"  Alert check interval: {config.ALERT_CHECK_INTERVAL}s")
    log.info(f"  Anomaly window: {config.ANOMALY_WINDOW}s")
    log.info(f"  Anomaly sigma: {config.ANOMALY_SIGMA}")
    log.info(f"  Digest interval: {config.DIGEST_INTERVAL_HOURS}h")
    log.info(f"  Daily report at: {config.REPORT_HOUR:02d}:{config.REPORT_MINUTE:02d}")
    log.info(f"  Webhook configured: {'Yes' if config.DISCORD_WEBHOOK else 'No'}")
    log.info("=" * 50)

    metrics.init_db()
    log.info(f"Database initialized at {config.DB_PATH}")

    baselines = metrics.get_baselines()
    total_points = sum(b["count"] for b in baselines.values()) if baselines else 0
    if total_points >= config.ANOMALY_MIN_BASELINES:
        log.info(f"Adaptive mode active ({total_points} baseline points)")
    else:
        log.info(f"Learning mode ({total_points}/{config.ANOMALY_MIN_BASELINES} baseline points needed)")

    scheduler = BlockingScheduler()

    scheduler.add_job(
        collect_job,
        IntervalTrigger(seconds=config.COLLECT_INTERVAL),
        id="collect",
        name="Metric Collection",
        max_instances=1,
    )

    scheduler.add_job(
        alert_job,
        IntervalTrigger(seconds=config.ALERT_CHECK_INTERVAL),
        id="alert",
        name="Alert Check",
        max_instances=1,
    )

    scheduler.add_job(
        report_job,
        CronTrigger(hour=config.REPORT_HOUR, minute=config.REPORT_MINUTE),
        id="report",
        name="Daily Report",
    )

    scheduler.add_job(
        prune_job,
        CronTrigger(hour=3, minute=0),
        id="prune",
        name="Data Pruning",
    )

    scheduler.add_job(
        smart_job,
        CronTrigger(minute=0),
        id="smart",
        name="SMART Collection",
    )

    scheduler.add_job(
        baseline_job,
        CronTrigger(minute=5),
        id="baseline",
        name="Baseline Computation",
    )

    collect_job()
    log.info("Initial collection complete")

    def shutdown(signum, frame):
        log.info("Shutting down...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
