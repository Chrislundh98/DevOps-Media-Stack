#!/usr/bin/env python3
import os
import shutil
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from trackers.lib import DiscordNotifier

MONITORED_PATHS = [
    "/volume1",
    "/volume2"
]
WARNING_THRESHOLD = 90
CRITICAL_THRESHOLD = 95

base_dir = Path(__file__).parent.parent
log_dir = base_dir / 'logs' / 'system'
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'disk_monitor.log'),
        logging.StreamHandler()
    ]
)

notifier = DiscordNotifier()

def check_disk_usage():
    logging.info("Disk Monitor - Starting")
    
    alerts = []
    
    for path in MONITORED_PATHS:
        try:
            usage = shutil.disk_usage(path)
            percent_used = (usage.used / usage.total) * 100
            
            logging.info(f"{path}: {percent_used:.1f}% used ({usage.free / (1024**3):.2f} GB free)")
            
            if percent_used >= CRITICAL_THRESHOLD:
                alerts.append(f"🔴 **CRITICAL**: {path} at {percent_used:.1f}% capacity")
            elif percent_used >= WARNING_THRESHOLD:
                alerts.append(f"⚠️ **WARNING**: {path} at {percent_used:.1f}% capacity")
                
        except Exception as e:
            logging.error(f"Error checking {path}: {e}")
    
    if alerts:
        notifier.send_error(
            "\n".join(alerts),
            context="Disk Space Monitor"
        )

if __name__ == "__main__":
    check_disk_usage()
