import logging
import os
from qbittorrentapi import Client, APIConnectionError

class QbitClient:
    
    def __init__(self, url=None, username=None, password=None):
        self.url = url or os.getenv('QBIT_URL')
        self.username = username or os.getenv('QBIT_USER')
        self.password = password or os.getenv('QBIT_PASS')
        self.client = None
    
    def __enter__(self):
        self.connect()
        return self.client
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
    
    def connect(self):
        if not all([self.url, self.username, self.password]):
            raise ValueError("qBittorrent credentials not configured")
        
        try:
            self.client = Client(
                host=self.url,
                username=self.username,
                password=self.password
            )
            self.client.auth_log_in()
            logging.info(f"Connected to qBittorrent {self.client.app.version}")
            return self.client
        except APIConnectionError as e:
            logging.error(f"Failed to connect to qBittorrent: {e}")
            raise
    
    def disconnect(self):
        if self.client:
            try:
                self.client.auth_log_out()
                logging.info("Disconnected from qBittorrent")
            except Exception as e:
                logging.warning(f"Error during disconnect: {e}")
            finally:
                self.client = None
    
    def get_torrents_by_tracker(self, tracker_patterns):
        if not self.client:
            raise RuntimeError("Client not connected. Use connect() or context manager.")
        
        all_torrents = self.client.torrents_info()
        filtered = []
        
        for torrent in all_torrents:
            for tracker in torrent.trackers:
                tracker_url = tracker.get('url', '').lower()
                if any(pattern in tracker_url for pattern in tracker_patterns):
                    filtered.append(torrent)
                    break
        
        return filtered
    
    def get_torrents_by_category(self, category):
        if not self.client:
            raise RuntimeError("Client not connected")
        
        return self.client.torrents_info(category=category)
    
    def delete_torrents(self, torrent_hashes, delete_files=True):
        if not self.client:
            raise RuntimeError("Client not connected")
        
        if isinstance(torrent_hashes, str):
            torrent_hashes = [torrent_hashes]
        
        self.client.torrents_delete(torrent_hashes=torrent_hashes, delete_files=delete_files)
        logging.info(f"Deleted {len(torrent_hashes)} torrents (delete_files={delete_files})")
