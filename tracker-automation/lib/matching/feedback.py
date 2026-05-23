import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger('ml_feedback')

class FeedbackLoop:

    def __init__(self, json_dir, model_dir):
        self.json_dir = Path(json_dir)
        self.model_dir = Path(model_dir)
        self.predictions_log = self.model_dir / 'predictions_log.json'
        self.feedback_report = self.model_dir / 'feedback_report.json'
        self.pending_path = self.model_dir / 'pending_feedback.json'

    def log_predictions(self, predictions):
        existing = []
        if self.predictions_log.exists():
            try:
                with open(self.predictions_log) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing = []

        existing.extend(predictions)

        cutoff = (datetime.now() - timedelta(days=60)).isoformat()
        existing = [p for p in existing if p.get('timestamp', '') > cutoff]

        with open(self.predictions_log, 'w') as f:
            json.dump(existing, f, indent=2)

    def check_false_positives(self, hnr_torrents):
        if not self.predictions_log.exists():
            return []

        with open(self.predictions_log) as f:
            predictions = json.load(f)

        if not predictions:
            return []

        removals_path = self.json_dir / 'recent_removals_cache.json'
        if not removals_path.exists():
            return []

        with open(removals_path) as f:
            removals_data = json.load(f)
        removals = removals_data.get('removals', [])

        removed_hashes = {r.get('qbit_hash') for r in removals if r.get('qbit_hash')}
        removed_names = {r.get('qbit_name', '').lower() for r in removals}

        hnr_names = set()
        for h in hnr_torrents:
            if isinstance(h, dict):
                hnr_names.add(h.get('name', '').lower())
            elif isinstance(h, str):
                hnr_names.add(h.lower())

        false_positives = []
        for pred in predictions:
            qhash = pred.get('qbit_hash', '')
            qname = pred.get('qbit_name', '').lower()

            was_removed = qhash in removed_hashes or qname in removed_names

            if not was_removed:
                continue

            reappeared_as_hnr = False
            tracker_name = pred.get('tracker_name', '').lower()
            for hn in hnr_names:
                from .normalizer import NameNormalizer
                norm_hn = NameNormalizer.normalize(hn)
                norm_tn = NameNormalizer.normalize(tracker_name)
                if norm_hn == norm_tn or hn in tracker_name or tracker_name in hn:
                    reappeared_as_hnr = True
                    break

            if reappeared_as_hnr:
                false_positives.append({
                    'tracker_name': pred.get('tracker_name'),
                    'qbit_name': pred.get('qbit_name'),
                    'probability': pred.get('probability'),
                    'method': pred.get('method'),
                    'matched_at': pred.get('timestamp'),
                    'detected_at': datetime.now().isoformat(),
                    'tracker_size_mb': pred.get('tracker_size_mb'),
                    'qbit_size_mb': pred.get('qbit_size_mb'),
                })

        if false_positives:
            self._record_false_positives(false_positives)
            self._add_negative_feedback(false_positives)

        return false_positives

    def check_removals_missing_or_errored(self, qbit_torrents):
        removals_path = self.json_dir / 'recent_removals_cache.json'
        if not removals_path.exists():
            return []

        with open(removals_path) as f:
            removals_data = json.load(f)
        removals = removals_data.get('removals', [])

        current_hashes = set()
        for qt in qbit_torrents:
            h = getattr(qt, 'hash', None) or (qt.get('hash') if isinstance(qt, dict) else None)
            if h:
                current_hashes.add(h)

        suspicious = []
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

        for r in removals:
            if r.get('removed_at', '') > cutoff:
                continue
            rhash = r.get('qbit_hash', '')
            if rhash and rhash in current_hashes:
                suspicious.append({
                    'removal': r,
                    'issue': 'still_present_after_removal',
                    'detected_at': datetime.now().isoformat(),
                })

        return suspicious

    def verify_successful_matches(self, active_qbit_hashes):
        if not self.predictions_log.exists():
            return 0

        with open(self.predictions_log) as f:
            predictions = json.load(f)

        confirmed = 0
        verification_cutoff = (datetime.now() - timedelta(days=7)).isoformat()

        for pred in predictions:
            if pred.get('verified'):
                continue
            if pred.get('timestamp', '') > verification_cutoff:
                continue
            qhash = pred.get('qbit_hash', '')
            if qhash and qhash in active_qbit_hashes:
                pred['verified'] = True
                pred['verified_at'] = datetime.now().isoformat()
                confirmed += 1

                self._add_positive_feedback(pred)

        with open(self.predictions_log, 'w') as f:
            json.dump(predictions, f, indent=2)

        return confirmed

    def _record_false_positives(self, fps):
        report = []
        if self.feedback_report.exists():
            try:
                with open(self.feedback_report) as f:
                    report = json.load(f)
            except (json.JSONDecodeError, IOError):
                report = []

        report.extend(fps)

        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        report = [r for r in report if r.get('detected_at', '') > cutoff]

        with open(self.feedback_report, 'w') as f:
            json.dump(report, f, indent=2)

        logger.warning(f"Detected {len(fps)} ML false positive(s)!")
        for fp in fps:
            logger.warning(f"  FP: '{fp['tracker_name']}' incorrectly matched to '{fp['qbit_name']}' "
                           f"(prob={fp['probability']:.3f})")

    def _add_negative_feedback(self, false_positives):
        pending = {'feedback': []}
        if self.pending_path.exists():
            try:
                with open(self.pending_path) as f:
                    pending = json.load(f)
            except (json.JSONDecodeError, IOError):
                pending = {'feedback': []}

        for fp in false_positives:
            pending['feedback'].append({
                'tracker_name': fp['tracker_name'],
                'qbit_name': fp['qbit_name'],
                'label': 0,
                'weight': 5.0,
                'tracker_size_mb': fp.get('tracker_size_mb'),
                'qbit_size_mb': fp.get('qbit_size_mb'),
                'source': 'false_positive_detection',
                'added_at': datetime.now().isoformat(),
            })

        with open(self.pending_path, 'w') as f:
            json.dump(pending, f, indent=2)

    def _add_positive_feedback(self, prediction):
        pending = {'feedback': []}
        if self.pending_path.exists():
            try:
                with open(self.pending_path) as f:
                    pending = json.load(f)
            except (json.JSONDecodeError, IOError):
                pending = {'feedback': []}

        pending['feedback'].append({
            'tracker_name': prediction['tracker_name'],
            'qbit_name': prediction['qbit_name'],
            'label': 1,
            'weight': 2.0,
            'tracker_size_mb': prediction.get('tracker_size_mb'),
            'qbit_size_mb': prediction.get('qbit_size_mb'),
            'source': 'verified_still_seeding',
            'added_at': datetime.now().isoformat(),
        })

        with open(self.pending_path, 'w') as f:
            json.dump(pending, f, indent=2)

    def get_report(self):
        report = {
            'total_predictions_logged': 0,
            'verified_correct': 0,
            'detected_false_positives': 0,
            'pending_feedback': 0,
        }

        if self.predictions_log.exists():
            with open(self.predictions_log) as f:
                preds = json.load(f)
            report['total_predictions_logged'] = len(preds)
            report['verified_correct'] = sum(1 for p in preds if p.get('verified'))

        if self.feedback_report.exists():
            with open(self.feedback_report) as f:
                fps = json.load(f)
            report['detected_false_positives'] = len(fps)

        if self.pending_path.exists():
            with open(self.pending_path) as f:
                pending = json.load(f)
            report['pending_feedback'] = len(pending.get('feedback', []))

        return report
