import json
import logging
import os
import pickle
import random
import glob
import shutil
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np

from .features import extract_features, FEATURE_NAMES
from .normalizer import NameNormalizer

logger = logging.getLogger('ml_model')

MODEL_PARAMS = {
    'objective': 'binary',
    'metric': 'binary_logloss',
    'boosting_type': 'gbdt',
    'num_leaves': 63,
    'learning_rate': 0.05,
    'feature_fraction': 0.8,
    'bagging_fraction': 0.8,
    'bagging_freq': 5,
    'min_child_samples': 10,
    'lambda_l1': 0.1,
    'lambda_l2': 0.1,
    'verbose': -1,
    'n_jobs': -1,
    'seed': 42,
}

RETRAIN_THRESHOLD = 50   # retrain after 50 new feedback samples (was 200)
PERFORMANCE_ALERT_AUC = 0.90

class MatchModel:

    def __init__(self, model_dir, training_data_dir, json_dir):
        self.model_dir = Path(model_dir)
        self.training_data_dir = Path(training_data_dir)
        self.json_dir = Path(json_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.model_path = self.model_dir / 'match_model.pkl'
        self.meta_path = self.model_dir / 'model_meta.json'
        self.pending_path = self.model_dir / 'pending_feedback.json'

        self.model = None
        self.meta = self._load_meta()
        self._load_model()

    def _load_meta(self):
        if self.meta_path.exists():
            with open(self.meta_path) as f:
                return json.load(f)
        return {
            'trained_at': None,
            'training_samples': 0,
            'positive_samples': 0,
            'negative_samples': 0,
            'auc_score': None,
            'precision': None,
            'recall': None,
            'feature_importance': {},
            'retrain_count': 0,
            'pending_new_samples': 0,
            'false_positives_incorporated': 0,
        }

    def _save_meta(self):
        with open(self.meta_path, 'w') as f:
            json.dump(self.meta, f, indent=2)

    def _load_model(self):
        if self.model_path.exists():
            try:
                with open(self.model_path, 'rb') as f:
                    self.model = pickle.load(f)
                logger.info(f"Loaded model trained at {self.meta.get('trained_at')} "
                            f"(AUC={self.meta.get('auc_score', 'N/A')})")
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                self.model = None

    def is_ready(self):
        return self.model is not None

    def predict(self, features):
        if not self.is_ready():
            return None
        arr = np.array([features])
        return float(self.model.predict(arr)[0])

    def predict_batch(self, feature_list):
        if not self.is_ready():
            return None
        arr = np.array(feature_list)
        return self.model.predict(arr).tolist()

    def build_training_dataset(self):
        positives = []
        negatives = []
        session_qbit_names = []

        for fpath in sorted(glob.glob(str(self.training_data_dir / 'matching_training_data*.json'))):
            try:
                with open(fpath) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            for session in data.get('sessions', []):
                matches = session.get('matches', [])
                session_qbits = []

                for m in matches:
                    tracker_name = m.get('tracker_name')
                    qbit_name = m.get('matched_qbit_name') or m.get('qbit_name')
                    success = m.get('success', False)

                    if not tracker_name:
                        continue

                    tracker_size = m.get('tracker_size_mb')
                    qbit_size = m.get('qbit_size_mb')

                    candidates = m.get('top_candidates_considered', [])
                    if candidates:
                        for c in candidates:
                            cname = c.get('qbit_name')
                            csize = c.get('size_mb')
                            if cname:
                                session_qbits.append((cname, csize))

                    if success and qbit_name:
                        positives.append({
                            'tracker_name': tracker_name,
                            'qbit_name': qbit_name,
                            'tracker_size_mb': tracker_size,
                            'qbit_size_mb': qbit_size,
                            'label': 1,
                            'weight': 1.0,
                        })
                        if qbit_name:
                            session_qbits.append((qbit_name, qbit_size))
                    elif not success and qbit_name:
                        negatives.append({
                            'tracker_name': tracker_name,
                            'qbit_name': qbit_name,
                            'tracker_size_mb': tracker_size,
                            'qbit_size_mb': qbit_size,
                            'label': 0,
                            'weight': 1.0,
                        })
                    elif not success:
                        # No qbit_name but diagnosis may have a best_candidate
                        # Extract hard negatives from strategy-level diagnosis
                        diag = m.get('diagnosis', {})
                        for strat in diag.get('strategies_tried', []):
                            best_cand = strat.get('best_candidate')
                            best_score = strat.get('best_score', 0)
                            if best_cand and best_score and best_score > 0.3:
                                # This was a close miss but not a match — valuable hard negative
                                negatives.append({
                                    'tracker_name': tracker_name,
                                    'qbit_name': best_cand,
                                    'tracker_size_mb': tracker_size,
                                    'qbit_size_mb': None,
                                    'label': 0,
                                    'weight': 2.0,
                                })
                                session_qbits.append((best_cand, None))

                session_qbit_names.extend(session_qbits)

        verified_path = self.json_dir / 'verified_matches.json'
        if verified_path.exists():
            try:
                with open(verified_path) as f:
                    vm = json.load(f)
                for m in vm.get('matches', []):
                    positives.append({
                        'tracker_name': m['tracker_name'],
                        'qbit_name': m['qbit_name'],
                        'tracker_size_mb': m.get('tracker_size_mb'),
                        'qbit_size_mb': m.get('qbit_size_mb'),
                        'label': 1,
                        'weight': 3.0,
                    })
            except (json.JSONDecodeError, IOError):
                pass

        health_path = self.json_dir / 'algorithm_health.json'
        if health_path.exists():
            try:
                with open(health_path) as f:
                    health = json.load(f)
                fps = health.get('confirmed_false_positives', {})
                for inc in fps.get('recent_incidents', []):
                    hnr_name = inc.get('hnr_name', '')
                    matched_qbit = inc.get('matched_qbit_name', '')
                    if hnr_name and matched_qbit:
                        negatives.append({
                            'tracker_name': inc.get('original_tl_name', hnr_name),
                            'qbit_name': matched_qbit,
                            'tracker_size_mb': None,
                            'qbit_size_mb': None,
                            'label': 0,
                            'weight': 5.0,
                        })
            except (json.JSONDecodeError, IOError):
                pass

        # Lifecycle tracker: extract confirmed bad matches (HNR after removal, errored after match)
        lifecycle_path = self.json_dir / 'torrent_lifecycle.json'
        if lifecycle_path.exists():
            try:
                with open(lifecycle_path) as f:
                    lc = json.load(f)
                for entry in lc.values():
                    rev = entry.get('removal_event', {})
                    if not rev:
                        continue
                    tracker_name = rev.get('tracker_name', '')
                    qbit_name = entry.get('qbit_name', '')
                    # HNR events after removal = confirmed bad match
                    if entry.get('hnr_events') and tracker_name and qbit_name:
                        negatives.append({
                            'tracker_name': tracker_name,
                            'qbit_name': qbit_name,
                            'tracker_size_mb': None,
                            'qbit_size_mb': entry.get('size_mb'),
                            'label': 0,
                            'weight': 8.0,
                        })
                    # Error events where match_events exist = suspect bad match
                    elif entry.get('error_events') and entry.get('match_events') and tracker_name and qbit_name:
                        negatives.append({
                            'tracker_name': tracker_name,
                            'qbit_name': qbit_name,
                            'tracker_size_mb': None,
                            'qbit_size_mb': entry.get('size_mb'),
                            'label': 0,
                            'weight': 5.0,
                        })
            except (json.JSONDecodeError, IOError):
                pass

        pending_path = self.pending_path
        if pending_path.exists():
            try:
                with open(pending_path) as f:
                    pending = json.load(f)
                for entry in pending.get('feedback', []):
                    sample = {
                        'tracker_name': entry['tracker_name'],
                        'qbit_name': entry['qbit_name'],
                        'tracker_size_mb': entry.get('tracker_size_mb'),
                        'qbit_size_mb': entry.get('qbit_size_mb'),
                        'label': entry['label'],
                        'weight': entry.get('weight', 2.0),
                    }
                    if entry['label'] == 1:
                        positives.append(sample)
                    else:
                        negatives.append(sample)
            except (json.JSONDecodeError, IOError):
                pass

        neg_target = max(len(positives) * 3, len(negatives))
        extra_needed = neg_target - len(negatives)

        if extra_needed > 0 and session_qbit_names and positives:
            rng = random.Random(42)
            for _ in range(extra_needed):
                pos = rng.choice(positives)
                neg_qbit = rng.choice(session_qbit_names)
                if neg_qbit[0] == pos.get('qbit_name'):
                    continue
                negatives.append({
                    'tracker_name': pos['tracker_name'],
                    'qbit_name': neg_qbit[0],
                    'tracker_size_mb': pos.get('tracker_size_mb'),
                    'qbit_size_mb': neg_qbit[1],
                    'label': 0,
                    'weight': 0.5,
                })

        logger.info(f"Dataset: {len(positives)} positives, {len(negatives)} negatives")
        return positives + negatives

    def train(self, force=False):
        dataset = self.build_training_dataset()
        if len(dataset) < 100:
            logger.warning(f"Not enough training data ({len(dataset)} samples), skipping training")
            return False

        logger.info(f"Extracting features from {len(dataset)} samples...")
        features = []
        labels = []
        weights = []
        skipped = 0

        for sample in dataset:
            try:
                feat = extract_features(
                    sample['tracker_name'],
                    sample['qbit_name'],
                    tracker_size_mb=sample.get('tracker_size_mb'),
                    qbit_size_mb=sample.get('qbit_size_mb'),
                )
                features.append(feat)
                labels.append(sample['label'])
                weights.append(sample.get('weight', 1.0))
            except Exception as e:
                skipped += 1
                if skipped <= 5:
                    logger.debug(f"Skipped sample: {e}")

        if skipped > 0:
            logger.info(f"Skipped {skipped} samples during feature extraction")

        X = np.array(features)
        y = np.array(labels)
        w = np.array(weights)

        from sklearn.model_selection import train_test_split
        X_train, X_val, y_train, y_val, w_train, w_val = train_test_split(
            X, y, w, test_size=0.15, random_state=42, stratify=y
        )

        train_data = lgb.Dataset(X_train, label=y_train, weight=w_train,
                                 feature_name=FEATURE_NAMES, free_raw_data=False)
        val_data = lgb.Dataset(X_val, label=y_val, weight=w_val,
                               feature_name=FEATURE_NAMES, free_raw_data=False)

        callbacks = [lgb.log_evaluation(period=0)]
        model = lgb.train(
            MODEL_PARAMS,
            train_data,
            num_boost_round=500,
            valid_sets=[val_data],
            callbacks=callbacks + [lgb.early_stopping(stopping_rounds=30)],
        )

        val_preds = model.predict(X_val)

        from sklearn.metrics import roc_auc_score, precision_score, recall_score
        auc = roc_auc_score(y_val, val_preds)
        precision = precision_score(y_val, (val_preds > 0.5).astype(int), zero_division=0)
        recall = recall_score(y_val, (val_preds > 0.5).astype(int), zero_division=0)

        logger.info(f"Training complete: AUC={auc:.4f} Precision={precision:.4f} Recall={recall:.4f}")

        if self.model_path.exists():
            backup = self.model_dir / f'match_model_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pkl'
            shutil.copy2(self.model_path, backup)
            old_backups = sorted(glob.glob(str(self.model_dir / 'match_model_*.pkl')))
            for old in old_backups[:-5]:
                os.remove(old)

        self.model = model
        with open(self.model_path, 'wb') as f:
            pickle.dump(model, f)

        importance = dict(zip(FEATURE_NAMES, model.feature_importance(importance_type='gain').tolist()))
        self.meta.update({
            'trained_at': datetime.now().isoformat(),
            'training_samples': len(features),
            'positive_samples': int(np.sum(y)),
            'negative_samples': int(len(y) - np.sum(y)),
            'auc_score': round(auc, 4),
            'precision': round(precision, 4),
            'recall': round(recall, 4),
            'feature_importance': {k: round(v, 2) for k, v in
                                   sorted(importance.items(), key=lambda x: -x[1])},
            'retrain_count': self.meta.get('retrain_count', 0) + 1,
            'pending_new_samples': 0,
        })
        self._save_meta()

        if self.pending_path.exists():
            os.remove(self.pending_path)

        return True

    def add_feedback(self, tracker_name, qbit_name, label, tracker_size_mb=None,
                     qbit_size_mb=None, weight=2.0):
        if self.pending_path.exists():
            with open(self.pending_path) as f:
                pending = json.load(f)
        else:
            pending = {'feedback': []}

        pending['feedback'].append({
            'tracker_name': tracker_name,
            'qbit_name': qbit_name,
            'label': label,
            'tracker_size_mb': tracker_size_mb,
            'qbit_size_mb': qbit_size_mb,
            'weight': weight,
            'added_at': datetime.now().isoformat(),
        })

        with open(self.pending_path, 'w') as f:
            json.dump(pending, f, indent=2)

        self.meta['pending_new_samples'] = len(pending['feedback'])
        self._save_meta()

    def should_retrain(self):
        return self.meta.get('pending_new_samples', 0) >= RETRAIN_THRESHOLD

    def check_and_retrain(self):
        if self.should_retrain():
            logger.info(f"Retraining triggered: {self.meta['pending_new_samples']} pending samples")
            return self.train()
        return False

    def get_status(self):
        return {
            'model_loaded': self.is_ready(),
            'trained_at': self.meta.get('trained_at'),
            'auc_score': self.meta.get('auc_score'),
            'precision': self.meta.get('precision'),
            'recall': self.meta.get('recall'),
            'training_samples': self.meta.get('training_samples'),
            'pending_feedback': self.meta.get('pending_new_samples', 0),
            'retrain_count': self.meta.get('retrain_count', 0),
            'retrain_threshold': RETRAIN_THRESHOLD,
            'top_features': dict(list(self.meta.get('feature_importance', {}).items())[:10]),
        }
