"""
RetryQueue — persistent queue for failed torrent matches.

When the main matching cascade produces no result, the tracker torrent is
pushed here with a timestamp and attempt count.  On subsequent monitor runs
the queue is drained *before* processing new stalled torrents so that
long-unmatched items get another shot with progressively relaxed parameters.

Strategy escalation per attempt:
  attempt 1  →  confidence HIGH=0.70 / MEDIUM=0.40  (release-group veto active)
  attempt 2  →  confidence HIGH=0.60 / MEDIUM=0.35  (release-group veto disabled)
  attempt 3  →  confidence HIGH=0.50 / MEDIUM=0.28  (year veto also disabled)
  attempt 4+ →  mark exhausted, send Discord alert

Retry back-off schedule (hours between attempts): 2, 6, 24
Storage: storage/json/retry_queue.json
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger('retry_queue')

# Hours to wait before each retry (index = attempt number that just failed)
BACKOFF_HOURS = [2, 6, 24]

# (confidence_high, confidence_medium) per attempt number (1-indexed)
STRATEGY_THRESHOLDS = {
    1: (0.70, 0.40),
    2: (0.60, 0.35),
    3: (0.50, 0.28),
}

MAX_ATTEMPTS = 3

class RetryQueue:

    def __init__(self, json_dir):
        self.path = Path(json_dir) / 'retry_queue.json'

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

    def push(self, tracker_name, tracker_tag, tracker_size_mb, failure_reason):
        """Add a failed match to the retry queue (idempotent — no duplicates)."""
        data = self._load()
        for item in data['items']:
            if item['tracker_name'] == tracker_name and item['status'] == 'pending':
                logger.debug(f"RetryQueue: already queued '{tracker_name[:50]}'")
                return

        next_retry = (datetime.now() + timedelta(hours=BACKOFF_HOURS[0])).isoformat()
        data['items'].append({
            'tracker_name': tracker_name,
            'tracker_tag': tracker_tag,
            'tracker_size_mb': tracker_size_mb,
            'failure_reason': failure_reason,
            'attempt_count': 0,
            'added_at': datetime.now().isoformat(),
            'next_retry_at': next_retry,
            'status': 'pending',
        })
        self._save(data)
        logger.info(
            f"RetryQueue: queued '{tracker_name[:60]}' "
            f"(reason={failure_reason}, first retry in {BACKOFF_HOURS[0]}h)"
        )

    def pop_due(self):
        """Return pending items whose next_retry_at has passed (not removed yet)."""
        data = self._load()
        now = datetime.now().isoformat()
        return [
            item for item in data['items']
            if item['status'] == 'pending' and item.get('next_retry_at', '') <= now
        ]

    def mark_attempt_done(self, tracker_name, success):
        """After a retry attempt, either resolve or schedule the next attempt."""
        data = self._load()
        for item in data['items']:
            if item['tracker_name'] == tracker_name and item['status'] == 'pending':
                if success:
                    item['status'] = 'resolved'
                    item['resolved_at'] = datetime.now().isoformat()
                    logger.info(f"RetryQueue: resolved '{tracker_name[:60]}'")
                else:
                    item['attempt_count'] += 1
                    if item['attempt_count'] >= MAX_ATTEMPTS:
                        item['status'] = 'exhausted'
                        item['exhausted_at'] = datetime.now().isoformat()
                        logger.warning(
                            f"RetryQueue: EXHAUSTED '{tracker_name[:60]}' "
                            f"after {item['attempt_count']} attempts"
                        )
                    else:
                        backoff_idx = min(item['attempt_count'], len(BACKOFF_HOURS) - 1)
                        hours = BACKOFF_HOURS[backoff_idx]
                        item['next_retry_at'] = (
                            datetime.now() + timedelta(hours=hours)
                        ).isoformat()
                        logger.info(
                            f"RetryQueue: attempt {item['attempt_count']} failed for "
                            f"'{tracker_name[:50]}', next retry in {hours}h"
                        )
                break
        self._save(data)

    def get_strategy_params(self, item):
        """
        Return (confidence_high, confidence_medium, disable_rg_veto, disable_year_veto)
        appropriate for this item's current attempt number.
        """
        attempt = item.get('attempt_count', 0) + 1   # +1 because we're about to do attempt N
        thresholds = STRATEGY_THRESHOLDS.get(attempt, STRATEGY_THRESHOLDS[MAX_ATTEMPTS])
        c_high, c_medium = thresholds
        disable_rg_veto   = attempt >= 2
        disable_year_veto = attempt >= 3
        return c_high, c_medium, disable_rg_veto, disable_year_veto

    def get_exhausted_unalerted(self):
        """Return exhausted items that haven't been Discord-alerted yet."""
        data = self._load()
        return [
            i for i in data['items']
            if i['status'] == 'exhausted' and not i.get('alerted')
        ]

    def mark_alerted(self, tracker_names):
        """Mark exhausted items as alerted so they aren't re-reported."""
        data = self._load()
        name_set = set(tracker_names)
        for item in data['items']:
            if item['tracker_name'] in name_set and item['status'] == 'exhausted':
                item['alerted'] = True
        self._save(data)

    def purge_old(self, days=14):
        """Remove resolved/exhausted entries older than *days* days."""
        data = self._load()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        before = len(data['items'])
        data['items'] = [
            i for i in data['items']
            if i['status'] == 'pending'
            or i.get('resolved_at', i.get('exhausted_at', '')) > cutoff
        ]
        removed = before - len(data['items'])
        if removed:
            self._save(data)
            logger.info(f"RetryQueue: purged {removed} old entries ({len(data['items'])} remain)")
        return removed

    def stats(self):
        data = self._load()
        items = data['items']
        return {
            'pending':   sum(1 for i in items if i['status'] == 'pending'),
            'resolved':  sum(1 for i in items if i['status'] == 'resolved'),
            'exhausted': sum(1 for i in items if i['status'] == 'exhausted'),
        }
