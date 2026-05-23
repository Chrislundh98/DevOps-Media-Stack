#!/usr/bin/env python3
"""
Train the ML matching model from existing training data.

Run this once to bootstrap the model, then the system auto-retrains
as new feedback accumulates.

Usage:
    python3 train_model.py
    python3 train_model.py --force    # retrain even if model exists
    python3 train_model.py --status   # show model status
"""

import sys
import os
import logging
import argparse

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_PATH)
sys.path.insert(0, os.path.join(BASE_PATH, 'lib'))

from matching.ml_model import MatchModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(BASE_DIR, 'storage')
TRAINING_DIR = os.path.join(STORAGE_DIR, 'training')
JSON_DIR = os.path.join(STORAGE_DIR, 'json')
MODEL_DIR = os.path.join(STORAGE_DIR, 'ml_model')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

def main():
    parser = argparse.ArgumentParser(description='Train ML matching model')
    parser.add_argument('--force', action='store_true', help='Force retrain')
    parser.add_argument('--status', action='store_true', help='Show model status')
    args = parser.parse_args()

    model = MatchModel(MODEL_DIR, TRAINING_DIR, JSON_DIR)

    if args.status:
        status = model.get_status()
        print("\n=== ML Model Status ===")
        for k, v in status.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for fk, fv in v.items():
                    print(f"    {fk}: {fv}")
            else:
                print(f"  {k}: {v}")
        return

    if model.is_ready() and not args.force:
        print(f"Model already trained at {model.meta.get('trained_at')}")
        print(f"AUC={model.meta.get('auc_score')} | Use --force to retrain")
        return

    print("Building training dataset...")
    success = model.train(force=True)
    if success:
        status = model.get_status()
        print(f"\nModel trained successfully!")
        print(f"  AUC:       {status['auc_score']}")
        print(f"  Precision: {status['precision']}")
        print(f"  Recall:    {status['recall']}")
        print(f"  Samples:   {status['training_samples']}")
        print(f"\nTop features:")
        for feat, imp in list(status['top_features'].items())[:10]:
            print(f"  {feat}: {imp}")
    else:
        print("Training failed. Check logs.")
        sys.exit(1)

if __name__ == '__main__':
    main()
