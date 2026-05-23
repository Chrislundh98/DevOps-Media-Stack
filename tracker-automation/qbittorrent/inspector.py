#!/usr/bin/env python3
"""
Torrent Inspector — runs every 15 minutes via cron.

For every torrent in qBittorrent that is in an 'error' or 'missingFiles' state:
  1. Force-rechecks it (the original behaviour)
  2. Looks up whether our scraper recently matched & removed something related
  3. If so, queues a negative ML feedback sample (bad match → caused missing files)
  4. Sends a Discord alert with details

This closes the feedback loop: errored/missing → ML learns the match was wrong.
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import QbitClient
from lib.torrent_lifecycle import TorrentLifecycleTracker
from lib.matching.recovery_queue import RecoveryQueue
from lib.matching.ml_model import MatchModel

base_dir = Path(__file__).resolve().parent.parent
log_dir = base_dir / 'logs' / 'qbittorrent'
log_dir.mkdir(parents=True, exist_ok=True)

load_dotenv(base_dir.parent / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'inspector.log'),
        logging.StreamHandler(),
    ],
)

WEBHOOK_URL = os.getenv('DISCORD_TORRENT_HOOK', '')
JSON_DIR = base_dir / 'storage' / 'json'
MODEL_DIR = base_dir / 'storage' / 'ml_model'

# How far back to check for recent removals that might explain an error
REMOVAL_LOOKBACK_HOURS = 72

def _send_discord(embeds):
    if not WEBHOOK_URL:
        return
    import requests
    try:
        requests.post(
            WEBHOOK_URL,
            json={'username': 'qBit Inspector', 'embeds': embeds},
            headers={'Content-Type': 'application/json'},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        logging.error(f"Discord notification failed: {e}")

def _load_recent_removals():
    """Load the recent_removals_cache.json to correlate errored torrents with removed ones."""
    path = JSON_DIR / 'recent_removals_cache.json'
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get('removals', [])
    except (json.JSONDecodeError, IOError):
        return []

def _load_predictions_log():
    """Load ML predictions log to find if a now-errored torrent was recently matched."""
    path = MODEL_DIR / 'predictions_log.json'
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def _find_related_removal(torrent_name, torrent_hash, removals, predictions):
    """
    Try to find a recent removal that might be related to an errored torrent.
    Returns (removal_dict, source) or (None, None).

    Heuristic: if a torrent currently errored/missing was the QBIT TARGET of a
    recent ML match (i.e., its hash appears in predictions_log or its name
    appears in removals), the match was likely wrong.
    """
    name_lower = torrent_name.lower()
    cutoff = (datetime.now() - timedelta(hours=REMOVAL_LOOKBACK_HOURS)).isoformat()

    # Check predictions log: was this qbit torrent matched by ML recently?
    for pred in predictions:
        if pred.get('timestamp', '') < cutoff:
            continue
        if (pred.get('qbit_hash') == torrent_hash or
                pred.get('qbit_name', '').lower() == name_lower):
            return pred, 'predictions_log'

    # Check removals cache: was a similarly-named torrent removed recently?
    for rem in removals:
        if rem.get('removed_at', '') < cutoff:
            continue
        rem_qbit = rem.get('qbit_name', '').lower()
        if rem_qbit and (rem_qbit == name_lower or
                         name_lower in rem_qbit or rem_qbit in name_lower):
            return rem, 'removals_cache'

    return None, None

def run():
    lifecycle = TorrentLifecycleTracker(
        json_dir=str(JSON_DIR),
        model_dir=str(MODEL_DIR),
    )
    recovery_queue = RecoveryQueue(json_dir=str(JSON_DIR))
    removals    = _load_recent_removals()
    predictions = _load_predictions_log()

    with QbitClient() as qbt:
        all_torrents = qbt.torrents_info()

        # Sync lifecycle with current qBit state
        lifecycle.bulk_sync_from_qbit(all_torrents)

        error_torrents = [
            t for t in all_torrents
            if 'error' in t.state.lower() or 'missing' in t.state.lower()
        ]

        if not error_torrents:
            logging.info(f"Inspector: no errored/missing torrents found ({len(all_torrents)} total)")
            # Still check if retraining is due even on clean runs
            _maybe_retrain()
            return

        logging.warning(f"Inspector: found {len(error_torrents)} torrent(s) in error/missing state")

        rechecked = []
        suspected_bad_matches = []

        for t in error_torrents:
            logging.warning(f"  [{t.state}] {t.name[:70]}")

            # Record the error state in lifecycle
            lifecycle.on_state_change(t.hash, t.state)

            # Force recheck
            try:
                qbt.torrents_recheck(torrent_hashes=t.hash)
                logging.info(f"  → Force recheck sent for: {t.name[:60]}")
                lifecycle.on_error_recovery(t.hash, recovery_type='force_recheck')
                rechecked.append(t)
            except Exception as e:
                logging.error(f"  → Recheck failed for {t.name[:50]}: {e}")

            # Check if a recent ML match / removal might explain the error
            related, source = _find_related_removal(t.name, t.hash, removals, predictions)
            if related:
                tracker_name = related.get('tracker_name') or related.get('tl_name', '')
                match_score  = related.get('probability') or related.get('match_score')
                match_method = related.get('method') or related.get('match_method', 'unknown')
                tracker_size = related.get('tracker_size_mb')
                tracker_tag  = related.get('tracker_tag', 'TL')
                qbit_size_mb = t.size / (1024 * 1024) if hasattr(t, 'size') else None

                logging.warning(
                    f"  → SUSPECTED BAD MATCH: torrent is errored and was recently "
                    f"referenced in {source} as '{tracker_name[:50]}' "
                    f"(score={match_score}, method={match_method})"
                )

                # 1. Queue negative ML feedback — this match caused an errored state
                lifecycle.queue_error_feedback(
                    qbit_hash=t.hash,
                    qbit_name=t.name,
                    tracker_name=tracker_name,
                    tracker_size_mb=tracker_size,
                    qbit_size_mb=qbit_size_mb,
                    match_method=match_method,
                )

                # 2. Queue for re-matching on the next monitor run
                if tracker_name:
                    recovery_queue.push(
                        tracker_name=tracker_name,
                        bad_qbit_hash=t.hash,
                        bad_qbit_name=t.name,
                        tracker_size_mb=tracker_size,
                        tracker_tag=tracker_tag,
                    )

                suspected_bad_matches.append({
                    'qbit_name':    t.name,
                    'qbit_state':   t.state,
                    'tracker_name': tracker_name,
                    'match_score':  match_score,
                    'match_method': match_method,
                    'source':       source,
                })

        # Discord notification
        if WEBHOOK_URL and rechecked:
            _notify_discord(rechecked, suspected_bad_matches)

        logging.info(
            f"Inspector: rechecked {len(rechecked)}, "
            f"suspected bad matches: {len(suspected_bad_matches)}"
        )

    # Trigger ML retraining if enough feedback has accumulated
    _maybe_retrain()

def _maybe_retrain():
    """Trigger ML model retraining if the pending-feedback threshold is met."""
    try:
        training_dirs = list((base_dir / 'storage' / 'training').glob(
            'matching_training_data*.json'))
        if not training_dirs:
            return
        model = MatchModel(
            model_dir=str(MODEL_DIR),
            training_data_dir=str(base_dir / 'storage' / 'training'),
            json_dir=str(JSON_DIR),
        )
        if model.should_retrain():
            pending = model.meta.get('pending_new_samples', 0)
            logging.info(f"Inspector: triggering ML retrain ({pending} pending samples)")
            success = model.train()
            if success:
                logging.info(
                    f"Inspector: retrain complete — "
                    f"AUC={model.meta.get('auc_score', 'N/A')}, "
                    f"precision={model.meta.get('precision', 'N/A')}"
                )
            else:
                logging.warning("Inspector: retrain attempted but failed (check logs)")
        else:
            pending = model.meta.get('pending_new_samples', 0)
            logging.info(f"Inspector: retrain not due yet ({pending} pending samples)")
    except Exception as e:
        logging.error(f"Inspector: retrain check failed: {e}")

def _notify_discord(rechecked, suspected_bad_matches):
    lines = []
    for t in rechecked:
        state_emoji = '❌' if 'error' in t.state.lower() else '⚠️'
        size_gb = round(t.size / (1024 ** 3), 2) if hasattr(t, 'size') else '?'
        lines.append(f"{state_emoji} `{t.name[:60]}` ({size_gb} GB) — *{t.state}*")

    desc = '\n'.join(lines[:20])
    if len(lines) > 20:
        desc += f'\n... and {len(lines) - 20} more'

    color = 15158332  # red
    fields = []

    if suspected_bad_matches:
        bad_lines = []
        for m in suspected_bad_matches[:5]:
            score_str = f"{m['match_score']:.3f}" if m.get('match_score') else 'N/A'
            bad_lines.append(
                f"• `{m['qbit_name'][:45]}` matched as `{m['tracker_name'][:35]}` "
                f"(score={score_str}, method={m['match_method']}) — negative feedback queued"
            )
        fields.append({
            'name': f'🤖 Suspected Bad ML Matches ({len(suspected_bad_matches)})',
            'value': '\n'.join(bad_lines) or 'None',
            'inline': False,
        })

    embeds = [{
        'title': f'⚠️ qBit Inspector: {len(rechecked)} Torrents Rechecked',
        'description': desc or 'No details',
        'color': color,
        'fields': fields,
        'footer': {'text': f'Checked at {datetime.now().strftime("%Y-%m-%d %H:%M")}'},
    }]
    _send_discord(embeds)

if __name__ == '__main__':
    run()
