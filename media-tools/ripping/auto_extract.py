#!/usr/bin/env python3
import os
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv

DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "/mnt/storage/data/downloads")
EXTRACT_PATH = os.getenv("EXTRACT_PATH", "/mnt/storage/data/extracted")

base_dir = Path(__file__).parent.parent.parent
log_dir = base_dir / 'logs' / 'media'
log_dir.mkdir(parents=True, exist_ok=True)

load_dotenv(base_dir / '.env')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'auto_extract.log'),
        logging.StreamHandler()
    ]
)

def find_rar_files():
    rars = []
    for root, _, files in os.walk(DOWNLOAD_PATH):
        for file in files:
            if file.endswith('.rar') and not file.startswith('part') or file.endswith('.part01.rar'):
                rars.append(Path(root) / file)
    return rars

def extract_rar(rar_path):
    logging.info(f"Extracting: {rar_path}")
    
    output_dir = Path(EXTRACT_PATH) / rar_path.parent.name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        subprocess.run(
            ['unrar', 'x', '-o+', str(rar_path), str(output_dir)],
            check=True,
            capture_output=True
        )
        logging.info(f"Successfully extracted to {output_dir}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to extract {rar_path}: {e}")
        return False

def main():
    logging.info("Auto Extractor - Starting")
    
    rars = find_rar_files()
    logging.info(f"Found {len(rars)} RAR archives")
    
    for rar in rars:
        extract_rar(rar)
    
    logging.info("Completed")

if __name__ == "__main__":
    main()
