#!/usr/bin/env python3
import os
import subprocess
import logging
from pathlib import Path

TARGET_PATHS = [
    "/volume2/media",
    "/volume2/data/downloads"
]
TARGET_USER = "your-user"
TARGET_GROUP = "admin"

base_dir = Path(__file__).parent.parent
log_dir = base_dir / 'logs' / 'system'
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'ownership.log'),
        logging.StreamHandler()
    ]
)

def fix_ownership():
    logging.info("Ownership Fixer - Starting")
    
    for path in TARGET_PATHS:
        if not os.path.exists(path):
            logging.warning(f"Path does not exist: {path}")
            continue
        
        logging.info(f"Fixing ownership for: {path}")
        
        try:
            subprocess.run(
                ['chown', '-R', f'{TARGET_USER}:{TARGET_GROUP}', path],
                check=True
            )
            logging.info(f"Successfully updated ownership for {path}")
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to update ownership for {path}: {e}")

if __name__ == "__main__":
    fix_ownership()
