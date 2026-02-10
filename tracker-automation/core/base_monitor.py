import logging
import os
import sys
import json
import requests
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import TorrentMatcher, QbitClient, CloudflareBypass, CookieManager


class BaseMonitor(ABC):

    def __init__(self, tracker_name, log_dir, storage_dir):
        self.tracker_name = tracker_name
        self.log_dir = Path(log_dir)
        self.storage_dir = Path(storage_dir)
        self.storage_dir_txt = self.storage_dir.parent / 'txt'

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir_txt.mkdir(parents=True, exist_ok=True)

        self._setup_logging()

        self.webhook_url = os.getenv('DISCORD_TORRENT_HOOK')
        self.qbit = QbitClient()

        # All training data goes to storage/json/training/
        training_dir = self.storage_dir / 'training'
        training_dir.mkdir(parents=True, exist_ok=True)
        training_file = training_dir / f'matching_training_data_{tracker_name.lower()}.json'

        accuracy_file = self.storage_dir / 'accuracy_log.json'
        health_file = self.storage_dir / 'algorithm_health.json'

        self.matcher = TorrentMatcher(
            match_threshold=0.75,
            training_file=str(training_file),
            accuracy_file=str(accuracy_file),
            health_file=str(health_file)
        )

        self.driver = None

    def _setup_logging(self):
        log_file = self.log_dir / f'{self.tracker_name.lower()}_monitor.log'

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )

    def _send_discord(self, embeds, username=None):
        if not self.webhook_url:
            logging.warning("DISCORD_TORRENT_HOOK not configured")
            return

        data = {
            "username": username or f"{self.tracker_name} Monitor",
            "embeds": embeds
        }

        try:
            requests.post(
                self.webhook_url,
                json=data,
                headers={"Content-Type": "application/json"}
            ).raise_for_status()
            logging.info("Discord notification sent.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send Discord notification: {e}")

    def _send_error_notification(self, error_message, context=None):
        if not self.webhook_url:
            return

        content = str(error_message)
        if context:
            content = f"{error_message}\n\n**Context:** {context}"

        embeds = [{
            "title": "⚠️ Error Alert",
            "description": content[:4096],
            "color": 15158332,
            "footer": {"text": f"Context: {self.tracker_name} Monitor"}
        }]

        self._send_discord(embeds)

    @staticmethod
    def format_size(size_mb):
        if size_mb <= 0:
            return "0 MB"
        if size_mb < 1024:
            return f"{size_mb:.2f} MB"
        elif size_mb < 1024 * 1024:
            return f"{size_mb / 1024:.2f} GB"
        else:
            return f"{size_mb / (1024 * 1024):.2f} TB"

    @staticmethod
    def parse_size_to_mb(size_str):
        size_str = size_str.strip().lower()
        try:
            if size_str == "0 b" or size_str.startswith("0 b"):
                return 0.0

            import re
            size_str = re.sub(r'\s+', ' ', size_str)

            parts = size_str.split()
            if len(parts) < 2:
                return 0.0

            value = float(parts[0].replace(',', ''))
            unit = parts[1]

            if unit.startswith('t'):
                return value * 1024 * 1024
            elif unit.startswith('g'):
                return value * 1024
            elif unit.startswith('m'):
                return value
            elif unit.startswith('k'):
                return value / 1024
            elif unit.startswith('b'):
                return value / (1024 * 1024)

            return value
        except (ValueError, IndexError) as e:
            logging.error(f"Could not parse size string '{size_str}': {e}")
            return 0.0

    @abstractmethod
    def fix_hnr(self):
        pass

    @abstractmethod
    def scrape_seeding_torrents(self):
        pass

    @abstractmethod
    def apply_removal_rules(self, torrents):
        pass

    @abstractmethod
    def run(self):
        pass