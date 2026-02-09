#!/usr/bin/env python3
"""
qBittorrent Force Reannounce Loop - v2.1
- Iterates through the entire list of downloading torrents.
- After finishing the list, loops back from the beginning.
- Handles pause/resume logic conditionally:
  - >= 30 torrents: Pauses every 30 torrents processed.
  - < 30 torrents: Pauses every 15 minutes.
- Handles API connection errors with reconnection.
"""

import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from qbittorrentapi import Client, APIConnectionError

# --- Constants ---
REANNOUNCE_DELAY = 30
PAUSE_RESUME_INTERVAL = 30
TIME_BASED_PAUSE_INTERVAL = 15 * 60
PAUSE_DURATION = 30
POST_RESUME_DELAY = 60

# --- Setup ---
base_dir = Path(__file__).parent.parent
log_dir = base_dir / 'logs' / 'qbittorrent' / 'reannounce'
log_dir.mkdir(parents=True, exist_ok=True)
load_dotenv(base_dir.parent / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'reannounce_loop.log'),
        logging.StreamHandler()
    ]
)


class QBitReannounceLoop:
    def __init__(self):
        self.qbit_url = os.getenv("QBIT_URL")
        self.qbit_user = os.getenv("QBIT_USER")
        self.qbit_pass = os.getenv("QBIT_PASS")
        self.qbt_client = None
        self.last_pause_resume_time = 0
        self.torrents_processed_since_pause = 0
        self.iteration_count = 0

    def _connect_to_qbit(self):
        try:
            logging.info("Connecting to qBittorrent...")
            self.qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            self.qbt_client.auth_log_in()
            logging.info(f"✓ Connected to qBittorrent {self.qbt_client.app.version}")
            return True
        except APIConnectionError as e:
            logging.error(f"Failed to connect: {e}")
            return False
        except Exception as e:
            logging.error(f"Unexpected connection error: {e}")
            return False

    def _disconnect_from_qbit(self):
        if self.qbt_client:
            try:
                self.qbt_client.auth_log_out()
                logging.info("Disconnected from qBittorrent")
            except:
                pass
            self.qbt_client = None

    def _get_downloading_torrents(self):
        try:
            return self.qbt_client.torrents_info(filter='downloading')
        except APIConnectionError as e:
            logging.error("API Connection Error fetching torrents")
            raise e
        except Exception as e:
            logging.error(f"Error fetching downloading torrents: {e}")
            return []

    def _force_reannounce(self, torrent):
        try:
            try:
                current = self.qbt_client.torrents_info(torrent_hashes=torrent.hash)
            except APIConnectionError as e:
                raise e
            except Exception as e:
                logging.warning(f"  ⏭️  SKIPPED: Could not verify status: {e}")
                return "skipped"

            if not current:
                logging.info("  ⏭️  SKIPPED: Torrent no longer exists")
                return "skipped"

            state = current[0].state
            if state not in {'downloading', 'stalledDL', 'metaDL', 'forcedDL', 'allocating', 'queuedDL'}:
                logging.info(f"  ⏭️  SKIPPED: No longer downloading (state: {state})")
                return "skipped"

            try:
                self.qbt_client.torrents_reannounce(torrent_hashes=torrent.hash)
            except APIConnectionError as e:
                raise e
            except Exception as e:
                logging.error(f"  ✗ Failed to reannounce '{torrent.name}': {e}")
                return False

            logging.info(f"  ✓ Reannounced: '{torrent.name}' ({torrent.progress*100:.1f}%)")
            return True
        except APIConnectionError:
            raise
        except Exception as e:
            logging.error(f"  ✗ Unexpected failure: {e}")
            return False

    def _pause_all_torrents(self):
        try:
            logging.info("  Pausing ALL torrents...")
            self.qbt_client.torrents_pause(torrent_hashes='all')
            logging.info("  ✓ All torrents paused")
            return True
        except APIConnectionError as e:
            raise e
        except Exception as e:
            logging.error(f"  ✗ Failed to pause: {e}")
            return False

    def _resume_all_torrents(self):
        try:
            logging.info("  Force resuming ALL torrents...")
            self.qbt_client.torrents_set_force_start(torrent_hashes='all', enable=True)
            logging.info("  ✓ All torrents force resumed")
            return True
        except APIConnectionError as e:
            raise e
        except Exception as e:
            logging.error(f"  ✗ Failed to resume: {e}")
            return False

    def _do_pause_resume_cycle(self):
        logging.info("")
        logging.info("=" * 80)
        logging.info("PAUSE/RESUME CYCLE")
        logging.info("=" * 80)

        try:
            self._pause_all_torrents()
            logging.info(f"  Waiting {PAUSE_DURATION}s with all paused...")
            time.sleep(PAUSE_DURATION)
            self._resume_all_torrents()
            logging.info(f"  Waiting {POST_RESUME_DELAY}s after resume...")
            time.sleep(POST_RESUME_DELAY)
        except APIConnectionError as e:
            raise e
        except Exception as e:
            logging.error(f"Unexpected error in pause/resume: {e}")

        logging.info("=" * 80)

    def _handle_connection_loss(self):
        logging.warning("Connection lost. Reconnecting...")
        self._disconnect_from_qbit()
        time.sleep(15)
        if not self._connect_to_qbit():
            logging.error("Reconnect failed. Waiting 60s...")
            time.sleep(60)
        else:
            logging.info("✓ Reconnected.")

    def run(self):
        logging.info("=" * 80)
        logging.info("qBittorrent Force Reannounce Loop - Starting")
        logging.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info("=" * 80)
        logging.info(f"Config: delay={REANNOUNCE_DELAY}s, count_trigger={PAUSE_RESUME_INTERVAL}, "
                     f"time_trigger={TIME_BASED_PAUSE_INTERVAL/60:.0f}min, pause={PAUSE_DURATION}s")
        logging.info("=" * 80)

        self.last_pause_resume_time = time.time()
        self.torrents_processed_since_pause = 0
        self.iteration_count = 0

        if not self._connect_to_qbit():
            logging.error("Initial connection failed.")
            time.sleep(30)
            sys.exit(1)

        try:
            while True:
                self.iteration_count += 1
                logging.info(f"Starting iteration #{self.iteration_count}")

                try:
                    torrents = self._get_downloading_torrents()
                    total = len(torrents)

                    if not torrents:
                        logging.info("No downloading torrents. Waiting 60s...")
                        time.sleep(60)
                        continue

                    logging.info(f"Processing {total} downloading torrents...")

                    for i, torrent in enumerate(torrents, 1):
                        pause_mode = 'count' if total >= PAUSE_RESUME_INTERVAL else 'time'
                        now = time.time()
                        trigger = False

                        if pause_mode == 'count':
                            if self.torrents_processed_since_pause >= PAUSE_RESUME_INTERVAL:
                                trigger = True
                        else:
                            if (now - self.last_pause_resume_time) > TIME_BASED_PAUSE_INTERVAL:
                                trigger = True

                        if trigger:
                            self._do_pause_resume_cycle()
                            self.last_pause_resume_time = time.time()
                            self.torrents_processed_since_pause = 0

                        logging.info(f"[{i}/{total}] Processing: {torrent.name[:80]}...")
                        result = self._force_reannounce(torrent)
                        if result == True:
                            self.torrents_processed_since_pause += 1

                        if i < total:
                            logging.info(f"  Waiting {REANNOUNCE_DELAY}s...")
                            time.sleep(REANNOUNCE_DELAY)

                    logging.info(f"Iteration #{self.iteration_count} complete. Looping...")

                except APIConnectionError:
                    self._handle_connection_loss()
                    continue
                except Exception as e:
                    logging.error(f"Error in main loop: {e}", exc_info=True)
                    time.sleep(30)

        except KeyboardInterrupt:
            logging.info(f"Shutting down. Iterations completed: {self.iteration_count}")
        finally:
            self._disconnect_from_qbit()


if __name__ == "__main__":
    if not os.getenv("QBIT_URL"):
        print("WARNING: QBIT_URL/QBIT_USER/QBIT_PASS not set.")
        sys.exit(1)
    QBitReannounceLoop().run()
