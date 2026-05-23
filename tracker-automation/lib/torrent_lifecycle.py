"""
TorrentLifecycleTracker

Tracks every torrent by hash from arrival to removal with a full audit trail.
This data feeds the ML model with rich negative examples (bad matches that led
to HNR/errored/missing-files states) and positive examples (stable long-term seeds).

Storage: storage/json/torrent_lifecycle.json
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger('lifecycle')

RETENTION_DAYS = 90

class TorrentLifecycleTracker:

    def __init__(self, json_dir, model_dir):
        self.json_dir = Path(json_dir)
        self.model_dir = Path(model_dir)
        self.lifecycle_path = self.json_dir / 'torrent_lifecycle.json'
        self.pending_path = self.model_dir / 'pending_feedback.json'
        self._data = None

    # Internal helpers

    def _load(self):
        if self._data is not None:
            return self._data
        if self.lifecycle_path.exists():
            try:
                with open(self.lifecycle_path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}
        else:
            self._data = {}
        return self._data

    def _save(self):
        if self._data is None:
            return
        try:
            with open(self.lifecycle_path, 'w') as f:
                json.dump(self._data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save lifecycle data: {e}")

    def _now(self):
        return datetime.now().isoformat()

    # Public API

    def on_torrent_seen(self, qbit_hash, qbit_name, size_mb,
                        category='', tags='', tracker_url=''):
        """Safe to call repeatedly — only records first-seen timestamp."""
        data = self._load()
        if qbit_hash in data:
            data[qbit_hash]['last_seen'] = self._now()
            self._save()
            return

        data[qbit_hash] = {
            'qbit_hash': qbit_hash,
            'qbit_name': qbit_name,
            'size_mb': round(size_mb, 2),
            'category': category,
            'tags': tags,
            'tracker_url': tracker_url,
            'first_seen': self._now(),
            'last_seen': self._now(),
            'state_history': [],
            'match_events': [],
            'removal_event': None,
            'error_events': [],
            'hnr_events': [],
            'status': 'active',
        }
        self._save()
        logger.debug(f"Lifecycle: first_seen {qbit_hash[:8]} '{qbit_name[:50]}'")

    def on_state_change(self, qbit_hash, new_state, old_state=None):
        """Record a qBit state change."""
        data = self._load()
        if qbit_hash not in data:
            return
        entry = data[qbit_hash]
        entry['state_history'].append({
            'ts': self._now(),
            'state': new_state,
            'from': old_state,
        })
        entry['last_seen'] = self._now()
        if 'error' in new_state.lower() or 'missing' in new_state.lower():
            entry['error_events'].append({
                'ts': self._now(),
                'state': new_state,
                'recovery_type': None,
            })
            entry['status'] = 'errored'
        self._save()

    def on_match_attempt(self, qbit_hash, tracker_name, tracker_tag,
                         score, method, tracker_size_mb=None):
        """Record that this qBit torrent was identified as a match for a tracker torrent."""
        data = self._load()
        if qbit_hash not in data:
            return
        data[qbit_hash]['match_events'].append({
            'ts': self._now(),
            'tracker_name': tracker_name,
            'tracker_tag': tracker_tag,
            'score': round(score, 4),
            'method': method,
            'tracker_size_mb': tracker_size_mb,
        })
        self._save()

    def on_removal(self, qbit_hash, qbit_name, reason, scraper,
                   tracker_name=None, match_score=None, match_method=None,
                   size_mb=None, delete_files=True):
        """Record that a torrent was deleted from qBit."""
        data = self._load()
        if qbit_hash not in data:
            data[qbit_hash] = {
                'qbit_hash': qbit_hash,
                'qbit_name': qbit_name,
                'size_mb': round(size_mb or 0, 2),
                'first_seen': self._now(),
                'last_seen': self._now(),
                'match_events': [],
                'error_events': [],
                'hnr_events': [],
                'state_history': [],
                'removal_event': None,
                'status': 'active',
                'category': '',
                'tags': '',
                'tracker_url': '',
            }
        entry = data[qbit_hash]
        entry['removal_event'] = {
            'ts': self._now(),
            'reason': reason,
            'scraper': scraper,
            'tracker_name': tracker_name,
            'match_score': round(match_score, 4) if match_score is not None else None,
            'match_method': match_method,
            'delete_files': delete_files,
        }
        entry['status'] = 'removed'
        entry['last_seen'] = self._now()
        self._save()
        logger.info(
            f"Lifecycle: removed {qbit_hash[:8]} '{qbit_name[:50]}' "
            f"via {scraper} ({reason})"
        )

    def on_hnr_detected(self, tracker_name, tracker_tag,
                        qbit_hash=None, qbit_name=None):
        """Record a tracker-side HNR. If linked to a removal, queue negative ML feedback."""
        data = self._load()
        if qbit_hash and qbit_hash in data:
            entry = data[qbit_hash]
            entry['hnr_events'].append({
                'ts': self._now(),
                'tracker_name': tracker_name,
                'tracker_tag': tracker_tag,
            })
            if entry.get('status') == 'removed' and entry.get('removal_event'):
                self._queue_bad_match_feedback(entry, tracker_name)
            self._save()

    def on_error_recovery(self, qbit_hash, recovery_type='recheck'):
        """Record a recovery attempt for an errored torrent."""
        data = self._load()
        if qbit_hash not in data:
            return
        entry = data[qbit_hash]
        if entry.get('error_events'):
            entry['error_events'][-1]['recovery_type'] = recovery_type
        if entry.get('status') == 'errored':
            entry['status'] = 'active'
        self._save()

    # Queries

    def get_recently_removed(self, hours=48):
        data = self._load()
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        return [
            e for e in data.values()
            if e.get('removal_event', {}) and
            e['removal_event'].get('ts', '') > cutoff
        ]

    def get_errored_entries(self):
        data = self._load()
        return [e for e in data.values() if e.get('status') == 'errored']

    def get_active_count(self):
        data = self._load()
        return sum(1 for e in data.values() if e.get('status') == 'active')

    def bulk_sync_from_qbit(self, all_qbit_torrents):
        """Sync all current qBit torrents into lifecycle (call at start of monitor run)."""
        for t in all_qbit_torrents:
            h = getattr(t, 'hash', None)
            if not h:
                continue
            name = getattr(t, 'name', '')
            size_mb = getattr(t, 'size', 0) / (1024 * 1024)
            category = getattr(t, 'category', '')
            tags = getattr(t, 'tags', '')
            trackers = getattr(t, 'trackers', [])
            tracker_url = ''
            for tr in trackers:
                url = tr.get('url', '')
                if url and not url.startswith('** '):
                    tracker_url = url
                    break
            self.on_torrent_seen(h, name, size_mb, category, tags, tracker_url)

    # Maintenance

    def purge_old_entries(self):
        data = self._load()
        cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
        to_delete = []
        for h, entry in data.items():
            if entry.get('status') == 'active':
                continue
            rev = entry.get('removal_event')
            last = rev.get('ts', '') if rev else entry.get('last_seen', '')
            if last < cutoff:
                to_delete.append(h)
        for h in to_delete:
            del data[h]
        if to_delete:
            self._save()
            logger.info(f"Lifecycle: purged {len(to_delete)} old entries ({len(data)} remain)")
        return len(data)

    # ML feedback helpers

    def _queue_bad_match_feedback(self, entry, hnr_tracker_name):
        """Bad match confirmed (removed torrent reappeared as HNR) → heavy negative sample."""
        rev = entry.get('removal_event', {})
        tracker_name = rev.get('tracker_name') or hnr_tracker_name
        qbit_name = entry.get('qbit_name', '')
        if not tracker_name or not qbit_name:
            return
        self._add_to_pending({
            'tracker_name': tracker_name,
            'qbit_name': qbit_name,
            'label': 0,
            'weight': 8.0,
            'tracker_size_mb': None,
            'qbit_size_mb': entry.get('size_mb'),
            'source': 'lifecycle_hnr_after_removal',
            'match_score': rev.get('match_score'),
            'match_method': rev.get('match_method'),
        })
        logger.warning(
            f"Lifecycle: bad-match negative feedback queued: "
            f"'{tracker_name[:50]}' → '{qbit_name[:50]}' (w=8.0)"
        )

    def queue_error_feedback(self, qbit_hash, qbit_name, tracker_name,
                             tracker_size_mb=None, qbit_size_mb=None,
                             match_method=None):
        """Errored/missing torrent was recently matched — likely a false positive removal."""
        self._add_to_pending({
            'tracker_name': tracker_name,
            'qbit_name': qbit_name,
            'label': 0,
            'weight': 6.0,
            'tracker_size_mb': tracker_size_mb,
            'qbit_size_mb': qbit_size_mb,
            'source': 'lifecycle_errored_after_match',
            'match_method': match_method,
            'qbit_hash': qbit_hash,
        })
        logger.warning(
            f"Lifecycle: error-state negative feedback queued: "
            f"'{tracker_name[:50]}' → '{qbit_name[:50]}' (w=6.0)"
        )

    def _add_to_pending(self, sample):
        if self.pending_path.exists():
            try:
                with open(self.pending_path) as f:
                    pending = json.load(f)
            except (json.JSONDecodeError, IOError):
                pending = {'feedback': []}
        else:
            pending = {'feedback': []}
        sample['added_at'] = self._now()
        pending['feedback'].append(sample)
        with open(self.pending_path, 'w') as f:
            json.dump(pending, f, indent=2)
