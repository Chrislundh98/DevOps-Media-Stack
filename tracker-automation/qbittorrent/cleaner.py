#!/usr/bin/env python3
"""
Orphaned download cleaner.
Removes files/folders in the download directory that are no longer
tracked by any torrent in qBittorrent. Sacred (1_year_torrents) items
are protected unconditionally.
"""
import logging
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import QbitClient

# Updated paths for new server
SCAN_DIRS = [Path("/mnt/storage/data/downloads")]
SACRED_CATEGORY = "1_year_torrents"
EXCLUDED_NAMES = {"watch", "extracted", "incomplete", ".DS_Store", "@eaDir"}

base_dir = Path(__file__).resolve().parent.parent
log_dir = base_dir / "logs" / "qbittorrent"
log_dir.mkdir(parents=True, exist_ok=True)
load_dotenv(base_dir.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "orphaned_cleaner.log"),
        logging.StreamHandler(),
    ],
)

def get_item_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

def run(dry_run: bool = True):
    prefix = "[DRY RUN] " if dry_run else ""
    logging.info(f"{prefix}Orphaned Files Cleanup - Starting")

    with QbitClient() as qbt:
        all_torrents = qbt.torrents_info()
        active_names = {t.name for t in all_torrents}
        sacred_names = {t.name for t in all_torrents if t.category == SACRED_CATEGORY}

        logging.info(f"Active torrents: {len(active_names)}, Sacred: {len(sacred_names)}")

        orphaned: list[tuple[Path, int]] = []
        total_size = 0

        for scan_dir in SCAN_DIRS:
            if not scan_dir.exists():
                logging.warning(f"Scan directory does not exist: {scan_dir}")
                continue

            for item in scan_dir.iterdir():
                if item.name in EXCLUDED_NAMES or item.name.startswith("."):
                    continue

                if item.name in sacred_names:
                    logging.info(f"SACRED (protected): {item.name}")
                    continue

                if item.name in active_names:
                    continue

                try:
                    size = get_item_size(item)
                    orphaned.append((item, size))
                    total_size += size
                except Exception as e:
                    logging.error(f"Error sizing {item.name}: {e}")

        logging.info(f"Found {len(orphaned)} orphaned items ({total_size / (1024**3):.2f} GB)")

        if not orphaned:
            logging.info("Nothing to clean up.")
            return

        for item, size in orphaned:
            size_str = f"{size / (1024**3):.2f} GB" if size > 1024**3 else f"{size / (1024**2):.1f} MB"
            if dry_run:
                logging.info(f"Would delete: {item.name} ({size_str})")
                continue

            try:
                if item.is_file():
                    item.unlink()
                else:
                    shutil.rmtree(item)
                logging.info(f"Deleted: {item.name} ({size_str})")
            except Exception as e:
                logging.error(f"Failed to delete {item.name}: {e}")

    logging.info(f"{prefix}Cleanup complete.")

if __name__ == "__main__":
    run(dry_run="--execute" not in sys.argv)
