import logging
import os
import sys
import json
import requests
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import MLMatcher, FeedbackLoop, QbitClient, CloudflareBypass, CookieManager
from lib import RetryQueue, RecoveryQueue
from lib.torrent_lifecycle import TorrentLifecycleTracker

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

        training_dir = self.storage_dir.parent / 'training'
        training_dir.mkdir(parents=True, exist_ok=True)
        training_file = training_dir / f'matching_training_data_{tracker_name.lower()}.json'
        accuracy_file = self.storage_dir / 'accuracy_log.json'
        health_file = self.storage_dir / 'algorithm_health.json'

        model_dir = self.storage_dir.parent / 'ml_model'
        model_dir.mkdir(parents=True, exist_ok=True)

        self.matcher = MLMatcher(
            model_dir=str(model_dir),
            training_data_dir=str(training_dir),
            json_dir=str(self.storage_dir),
            training_file=str(training_file),
            accuracy_file=str(accuracy_file),
            health_file=str(health_file),
        )

        self.feedback = FeedbackLoop(
            json_dir=str(self.storage_dir),
            model_dir=str(model_dir),
        )

        self.lifecycle = TorrentLifecycleTracker(
            json_dir=str(self.storage_dir),
            model_dir=str(model_dir),
        )

        self.retry_queue    = RetryQueue(json_dir=str(self.storage_dir))
        self.recovery_queue = RecoveryQueue(json_dir=str(self.storage_dir))

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

    # Retry queue — re-attempt previously failed matches

    def _drain_retry_queue(self, qbit_torrents, tracker_tag,
                           exclude_hashes=None):
        """
        Re-attempt all retry-queue items whose back-off period has elapsed.
        Each item is tried with progressively relaxed matching parameters.

        Returns (matched_list, still_failed_list) where matched_list contains
        dicts suitable for appending to the 'removed' list in _process_stalled_torrents.
        """
        due_items = self.retry_queue.pop_due()
        if not due_items:
            return [], []

        exclude = set(exclude_hashes or [])
        candidates = [t for t in qbit_torrents
                      if getattr(t, 'hash', None) not in exclude]

        matched = []
        still_failed = []

        for item in due_items:
            tracker_name   = item['tracker_name']
            tracker_tag_i  = item.get('tracker_tag', tracker_tag)
            tracker_size   = item.get('tracker_size_mb')
            attempt        = item.get('attempt_count', 0)

            c_high, c_medium, dis_rg, dis_year = self.retry_queue.get_strategy_params(item)

            logging.info(
                f"RetryQueue: attempt {attempt + 1} for '{tracker_name[:60]}' "
                f"(c_high={c_high}, c_medium={c_medium}, "
                f"dis_rg={dis_rg}, dis_year={dis_year})"
            )

            result = self.matcher.find_best_match(
                tracker_name, candidates, tracker_tag_i, tracker_size,
                confidence_high=c_high, confidence_medium=c_medium,
                disable_rg_veto=dis_rg, disable_year_veto=dis_year,
            )
            qt, score, method = result

            if qt:
                self.retry_queue.mark_attempt_done(tracker_name, success=True)
                logging.info(
                    f"RetryQueue: matched on attempt {attempt + 1}: "
                    f"'{tracker_name[:50]}' → '{qt.name[:50]}' ({score:.3f})"
                )
                matched.append({
                    'name':         tracker_name,
                    'size_mb':      tracker_size or (qt.size / (1024 * 1024)),
                    'reason':       item.get('failure_reason', 'retry'),
                    'match_method': f'retry_{attempt + 1}_{method}',
                    'match_score':  score,
                    '_qbit_torrent': qt,
                })
            else:
                self.retry_queue.mark_attempt_done(tracker_name, success=False)
                still_failed.append(tracker_name)

        # Alert Discord for items that have hit max attempts
        exhausted = self.retry_queue.get_exhausted_unalerted()
        if exhausted:
            self._notify_exhausted_retries(exhausted)
            self.retry_queue.mark_alerted([i['tracker_name'] for i in exhausted])

        # Housekeeping
        self.retry_queue.purge_old()

        return matched, still_failed

    def _notify_exhausted_retries(self, exhausted_items):
        """Send a Discord alert listing retry-exhausted torrents."""
        if not self.webhook_url:
            return
        lines = []
        for item in exhausted_items[:10]:
            lines.append(
                f"• `{item['tracker_name'][:70]}` "
                f"— {item['attempt_count']} attempts, "
                f"last failure: {item.get('failure_reason', '?')}"
            )
        desc = '\n'.join(lines) or 'No details'
        if len(exhausted_items) > 10:
            desc += f'\n… and {len(exhausted_items) - 10} more'
        embeds = [{
            'title': f'🔴 {len(exhausted_items)} Torrent(s) Exhausted All Retry Attempts',
            'description': desc,
            'color': 15158332,
            'footer': {'text': f'{self.tracker_name} Monitor — manual review needed'},
        }]
        self._send_discord(embeds)

    # Recovery queue — re-match after Inspector confirms a bad match

    def _drain_recovery_queue(self, stalled_torrents, qbit_torrents, tracker_tag):
        """
        For every pending recovery item, find the original stalled tracker torrent
        in the current run's stalled list, exclude the known-bad qBit hash, and
        re-attempt matching with slightly relaxed confidence.

        Returns (recovered_list, failed_names) where recovered_list has the same
        shape as the matched_list from _drain_retry_queue.
        """
        pending = self.recovery_queue.pop_all_pending()
        if not pending:
            return [], []

        # Build a fast lookup: normalised tracker name → stalled torrent dict
        from lib.matching.normalizer import NameNormalizer
        stalled_index = {}
        for t in stalled_torrents:
            norm = NameNormalizer.normalize(t['name'], tracker_tag)
            stalled_index[norm] = t
            stalled_index[t['name'].lower()] = t   # also raw lower

        recovered = []
        failed = []

        for item in pending:
            tracker_name  = item['tracker_name']
            bad_hash      = item.get('bad_qbit_hash')
            tracker_size  = item.get('tracker_size_mb')
            tracker_tag_i = item.get('tracker_tag', tracker_tag)

            # Locate the tracker torrent in the current stalled list
            norm_name = NameNormalizer.normalize(tracker_name, tracker_tag_i)
            tl_torrent = (stalled_index.get(norm_name)
                          or stalled_index.get(tracker_name.lower()))

            if not tl_torrent:
                # No longer stalled → torrent was already removed or resolved
                logging.info(
                    f"RecoveryQueue: '{tracker_name[:60]}' not in current stalled list "
                    f"— marking resolved (no longer needs removal)"
                )
                self.recovery_queue.mark_resolved(tracker_name, matched_to_name='not_stalled')
                continue

            # Exclude the known-bad candidate
            candidates = [t for t in qbit_torrents
                          if getattr(t, 'hash', None) != bad_hash]

            logging.info(
                f"RecoveryQueue: re-matching '{tracker_name[:60]}' "
                f"(excluding bad hash {(bad_hash or '?')[:12]})"
            )

            result = self.matcher.find_best_match(
                tracker_name, candidates, tracker_tag_i,
                tracker_size_mb=tracker_size or tl_torrent.get('size_mb'),
                confidence_high=0.65,
                confidence_medium=0.38,
                disable_rg_veto=False,
            )
            qt, score, method = result

            if qt:
                self.recovery_queue.mark_resolved(tracker_name, qt.name)
                logging.info(
                    f"RecoveryQueue: resolved '{tracker_name[:50]}' "
                    f"→ '{qt.name[:50]}' ({score:.3f})"
                )
                recovered.append({
                    'name':         tracker_name,
                    'size_mb':      tracker_size or tl_torrent.get('size_mb', 0),
                    'reason':       tl_torrent.get('reason', 'recovery'),
                    'match_method': f'recovery_{method}',
                    'match_score':  score,
                    '_qbit_torrent': qt,
                })
            else:
                self.recovery_queue.mark_failed(tracker_name)
                failed.append(tracker_name)
                # Hand off to retry queue for further attempts
                self.retry_queue.push(
                    tracker_name, tracker_tag_i,
                    tracker_size or tl_torrent.get('size_mb'),
                    failure_reason='recovery_failed',
                )

        self.recovery_queue.purge_old()
        return recovered, failed

    # Abstract interface

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