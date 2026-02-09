import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import QbitClient

class TorrentInspector:
    
    def __init__(self):
        base_dir = Path(__file__).parent.parent
        log_dir = base_dir / 'logs' / 'qbittorrent'
        log_dir.mkdir(parents=True, exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'inspector.log'),
                logging.StreamHandler()
            ]
        )
        
        self.qbit = QbitClient()
    
    def run(self):
        logging.info("Torrent Inspector - Starting")
        
        with self.qbit as qbt:
            all_torrents = qbt.torrents_info()
            
            error_torrents = [t for t in all_torrents if 'error' in t.state.lower() or 'missing' in t.state.lower()]
            
            if error_torrents:
                logging.warning(f"Found {len(error_torrents)} torrents with errors")
                
                for torrent in error_torrents:
                    logging.info(f"Rechecking: {torrent.name[:60]} (State: {torrent.state})")
                    try:
                        qbt.torrents_recheck(torrent_hashes=torrent.hash)
                    except Exception as e:
                        logging.error(f"Failed to recheck {torrent.name[:50]}: {e}")
            else:
                logging.info("No torrents with errors found")

if __name__ == "__main__":
    inspector = TorrentInspector()
    inspector.run()
