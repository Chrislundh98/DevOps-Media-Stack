import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import QbitClient

ONE_TIB = 1024 ** 4

class BigTorrentQueue:
    
    def __init__(self):
        base_dir = Path(__file__).parent.parent
        log_dir = base_dir / 'logs' / 'qbittorrent'
        log_dir.mkdir(parents=True, exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'big_queue.log'),
                logging.StreamHandler()
            ]
        )
        
        self.qbit = QbitClient()
    
    def run(self):
        logging.info("1 TiB+ Torrent Queue Manager - Starting")
        
        with self.qbit as qbt:
            incomplete = qbt.torrents_info(filter='downloading')
            big_torrents = [t for t in incomplete if t.size >= ONE_TIB]
            
            logging.info(f"Found {len(big_torrents)} torrents >= 1 TiB")
            
            if not big_torrents:
                logging.info("No big torrents in queue")
                return
            
            active = [t for t in big_torrents if t.state in {'downloading', 'forcedDL'}]
            
            if active:
                for t in active:
                    logging.info(f"Already downloading: {t.name[:60]} ({t.size / ONE_TIB:.2f} TiB)")
                return
            
            stopped = [t for t in big_torrents if t.state in {'stoppedDL', 'pausedDL', 'stalledDL', 'queuedDL'}]
            
            if stopped:
                next_torrent = stopped[0]
                logging.info(f"Starting: {next_torrent.name[:60]} ({next_torrent.size / ONE_TIB:.2f} TiB)")
                qbt.torrents_resume(torrent_hashes=next_torrent.hash)
            else:
                logging.info("No stopped big torrents to start")

if __name__ == "__main__":
    manager = BigTorrentQueue()
    manager.run()
