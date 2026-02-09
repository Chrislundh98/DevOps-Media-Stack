import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import QbitClient

class ForceSeeder:
    
    def __init__(self):
        base_dir = Path(__file__).parent.parent
        log_dir = base_dir / 'logs' / 'qbittorrent'
        log_dir.mkdir(parents=True, exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'force_seeder.log'),
                logging.StreamHandler()
            ]
        )
        
        self.qbit = QbitClient()
    
    def run(self):
        logging.info("Force Seed Script - Starting")
        
        with self.qbit as qbt:
            seeding_torrents = qbt.torrents_info(filter='seeding')
            logging.info(f"Found {len(seeding_torrents)} seeding torrents")
            
            to_force = [t.hash for t in seeding_torrents if not t.force_start]
            
            if to_force:
                logging.info(f"Force-starting {len(to_force)} torrents")
                qbt.torrents_set_force_start(torrent_hashes=to_force, enable=True)
            else:
                logging.info("All seeding torrents already force-started")

if __name__ == "__main__":
    seeder = ForceSeeder()
    seeder.run()
