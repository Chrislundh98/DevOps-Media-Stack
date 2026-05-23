"""
RecoveryQueue — tracks confirmed bad matches for re-matching on the next monitor run.

When the Inspector (qbittorrent/inspector.py) finds that a torrent which was
recently matched and removed is now in an error/missingFiles state, it writes
here.  The next monitor run reads the queue, excludes the known-bad qBit
torrent from the candidate pool, and re-attempts matching with relaxed
confidence thresholds.

Flow:
  Inspector detects bad match
      ↓
  recovery_queue.push(tracker_name, bad_qbit_hash, ...)
      ↓  (persisted across cron runs)
  Next monitor run: base_monitor._drain_recovery_queue()
      ↓
  find_best_match() excluding bad_qbit_hash, c_high=0.65
      ↓
  success → remove correct torrent
  failure → mark failed (retried by RetryQueue on subsequent runs)

Storage: storage/json/recovery_queue.json
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger('recovery_queue')

class RecoveryQueue:

    def __init__(self, json_dir):
        self.path = Path(json_dir) / 'recovery_queue.json'

    # Internal I/O

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {'items': []}

    def _save(self, data):
        with open(self.path, 'w') as f:
            json.dump(data, f, indent=2)

    # Public API

    def push(self, tracker_name, bad_qbit_hash, bad_qbit_name,
             tracker_size_mb, tracker_tag):
        """Record a confirmed bad match that needs re-matching (idempotent)."""
        data = self._load()
        for item in data['items']:
            if item['tracker_name'] == tracker_name and item['status'] == 'pending':
                logger.debug(f"RecoveryQueue: already pending '{tracker_name[:50]}'")
                return

        data['items'].append({
            'tracker_name':    tracker_name,
            'bad_qbit_hash':   bad_qbit_hash,
            'bad_qbit_name':   bad_qbit_name,
            'tracker_size_mb': tracker_size_mb,
            'tracker_tag':     tracker_tag,
            'added_at':        datetime.now().isoformat(),
            'status':          'pending',
        })
        self._save(data)
        logger.warning(
            f"RecoveryQueue: queued re-match for '{tracker_name[:60]}' "
            f"(bad candidate was '{bad_qbit_name[:50]}')"
        )

    def pop_all_pending(self):
        """Return all pending recovery items without removing them."""
        data = self._load()
        return [i for i in data['items'] if i['status'] == 'pending']

    def mark_resolved(self, tracker_name, matched_to_name=None):
        data = self._load()
        for item in data['items']:
            if item['tracker_name'] == tracker_name and item['status'] == 'pending':
                item['status'] = 'resolved'
                item['resolved_at'] = datetime.now().isoformat()
                item['matched_to'] = matched_to_name
                logger.info(
                    f"RecoveryQueue: resolved '{tracker_name[:60]}' "
                    f"→ '{(matched_to_name or '?')[:50]}'"
                )
                break
        self._save(data)

    def mark_failed(self, tracker_name):
        """Mark as failed — will be handed off to RetryQueue for further attempts."""
        data = self._load()
        for item in data['items']:
            if item['tracker_name'] == tracker_name and item['status'] == 'pending':
                item['status'] = 'failed'
                item['failed_at'] = datetime.now().isoformat()
                logger.warning(
                    f"RecoveryQueue: re-match failed for '{tracker_name[:60]}', "
                    f"handing to RetryQueue"
                )
                break
        self._save(data)

    def purge_old(self, days=7):
        """Remove resolved/failed entries older than *days* days."""
        data = self._load()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        before = len(data['items'])
        data['items'] = [
            i for i in data['items']
            if i['status'] == 'pending'
            or i.get('resolved_at', i.get('failed_at', '')) > cutoff
        ]
        removed = before - len(data['items'])
        if removed:
            self._save(data)
            logger.info(f"RecoveryQueue: purged {removed} old entries")
        return removed

    def stats(self):
        data = self._load()
        items = data['items']
        return {
            'pending':  sum(1 for i in items if i['status'] == 'pending'),
            'resolved': sum(1 for i in items if i['status'] == 'resolved'),
            'failed':   sum(1 for i in items if i['status'] == 'failed'),
        }
