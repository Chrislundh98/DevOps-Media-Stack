#!/usr/bin/env python3
import os
import subprocess
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from trackers.lib import DiscordNotifier

MOVIES_PATH = "/volume2/media/movies"
OUTPUT_PATH = "/volume2/data/remuxed"
CONTAINER_NAME = "makemkv"
MIN_TITLE_LENGTH_SECONDS = 1200

base_dir = Path(__file__).parent.parent.parent
log_dir = base_dir / 'logs' / 'media'
log_dir.mkdir(parents=True, exist_ok=True)

load_dotenv(base_dir / '.env')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'makemkv_ripper.log'),
        logging.StreamHandler()
    ]
)

notifier = DiscordNotifier()

def find_isos():
    isos = []
    for root, _, files in os.walk(MOVIES_PATH):
        for file in files:
            if file.lower().endswith('.iso'):
                isos.append(Path(root) / file)
    return isos

def rip_iso(iso_path):
    logging.info(f"Processing: {iso_path}")
    
    try:
        relative = iso_path.relative_to(MOVIES_PATH)
        container_path = f"/storage/{relative}"
        
        cmd = ["docker", "exec", CONTAINER_NAME, "makemkvcon", "-r", "info", f"iso:{container_path}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logging.error(f"Failed to scan ISO: {result.stderr}")
            return False
        
        logging.info(f"Successfully scanned {iso_path.name}")
        return True
        
    except Exception as e:
        logging.error(f"Error ripping {iso_path}: {e}")
        return False

def main():
    logging.info("MakeMKV ISO Ripper - Starting")
    
    isos = find_isos()
    logging.info(f"Found {len(isos)} ISO files")
    
    for iso in isos:
        rip_iso(iso)
    
    logging.info("Completed")

if __name__ == "__main__":
    main()
