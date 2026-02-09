import logging
import os
from pathlib import Path
from dotenv import load_dotenv
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import QbitClient

class OrphanedCleaner:
    
    def __init__(self, dry_run=True):
        base_dir = Path(__file__).parent.parent
        log_dir = base_dir / 'logs' / 'qbittorrent'
        log_dir.mkdir(parents=True, exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'orphaned_cleaner.log'),
                logging.StreamHandler()
            ]
        )
        
        self.dry_run = dry_run
        self.scan_dirs = ["/volume2/data/downloads"]
        self.qbit = QbitClient()
    
    def run(self):
        logging.info(f"{'DRY RUN - ' if self.dry_run else ''}Orphaned Files Cleanup")
        
        with self.qbit as qbt:
            all_torrents = qbt.torrents_info()
            active_names = {t.name for t in all_torrents}
            sacred_names = {t.name for t in all_torrents if t.category == "1_year_torrents"}
            
            logging.info(f"Active torrents: {len(active_names)}, Sacred: {len(sacred_names)}")
            
            orphaned = []
            total_size = 0
            
            for scan_dir in self.scan_dirs:
                scan_path = Path(scan_dir)
                if not scan_path.exists():
                    continue
                
                for item in scan_path.iterdir():
                    if item.name in active_names:
                        continue
                    
                    if item.name in sacred_names:
                        logging.warning(f"PROTECTED: {item.name}")
                        continue
                    
                    try:
                        if item.is_file():
                            size = item.stat().st_size
                        else:
                            size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                        
                        orphaned.append((item, size))
                        total_size += size
                        
                    except Exception as e:
                        logging.error(f"Error processing {item}: {e}")
            
            logging.info(f"Found {len(orphaned)} orphaned items ({total_size / (1024**3):.2f} GB)")
            
            if not self.dry_run and orphaned:
                for item, size in orphaned:
                    try:
                        if item.is_file():
                            item.unlink()
                        else:
                            import shutil
                            shutil.rmtree(item)
                        logging.info(f"Deleted: {item.name} ({size / (1024**3):.2f} GB)")
                    except Exception as e:
                        logging.error(f"Failed to delete {item}: {e}")

if __name__ == "__main__":
    dry_run = '--execute' not in sys.argv
    cleaner = OrphanedCleaner(dry_run=dry_run)
    cleaner.run()
