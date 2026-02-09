import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

class FileRotationManager:
    
    @staticmethod
    def check_and_rotate_file(file_path, max_size_mb=5, training_dir=None):
        if not os.path.exists(file_path):
            return False
        
        max_size_bytes = max_size_mb * 1024 * 1024
        file_size = os.path.getsize(file_path)
        
        if file_size <= max_size_bytes:
            return False
        
        if training_dir is None:
            training_dir = Path(file_path).parent.parent / 'training'
        
        training_dir = Path(training_dir)
        training_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        basename = Path(file_path).stem
        extension = Path(file_path).suffix
        archived_name = f"{basename}_{timestamp}{extension}"
        archived_path = training_dir / archived_name
        
        try:
            shutil.copy2(file_path, archived_path)
            logging.info(f"Rotated {file_size / (1024*1024):.1f}MB file to {archived_path}")
            
            with open(file_path, 'w') as f:
                if file_path.endswith('.json'):
                    json.dump({'summary': {}, 'sessions': []}, f, indent=2)
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to rotate file {file_path}: {e}")
            return False


class TrainingDataManager:
    
    def __init__(self, training_file, accuracy_file, health_file=None):
        self.training_file = training_file
        self.accuracy_file = accuracy_file
        self.health_file = health_file
        self.session_data = []
        
        Path(training_file).parent.mkdir(parents=True, exist_ok=True)
        if health_file:
            Path(health_file).parent.mkdir(parents=True, exist_ok=True)
    
    def add_match_attempt(self, match_data):
        self.session_data.append(match_data)
    
    def save(self):
        if not self.session_data:
            logging.info("No match attempts in current session to save.")
            return
        
        session_attempts = len(self.session_data)
        session_successes = sum(1 for m in self.session_data if m.get('success'))
        
        self._save_training_data(session_attempts, session_successes)
        self._update_accuracy_log(session_attempts, session_successes)
        
        if self.health_file:
            self._update_health_tracking()
        
        self.session_data = []
    
    def _save_training_data(self, attempts, successes):
        if os.path.exists(self.training_file):
            try:
                with open(self.training_file, 'r') as f:
                    data = json.load(f)
                    if not isinstance(data, dict) or 'sessions' not in data:
                        raise ValueError()
            except (json.JSONDecodeError, ValueError):
                data = {'summary': {}, 'sessions': []}
        else:
            data = {'summary': {}, 'sessions': []}
        
        data['sessions'].append({
            'timestamp': datetime.now().isoformat(),
            'total_attempts': attempts,
            'successes': successes,
            'failures': attempts - successes,
            'matches': self.session_data
        })
        
        total_attempts = sum(s.get('total_attempts', 0) for s in data['sessions'])
        total_successes = sum(s.get('successes', 0) for s in data['sessions'])
        
        data['summary'] = {
            'last_updated': datetime.now().isoformat(),
            'total_sessions': len(data['sessions']),
            'total_attempts': total_attempts,
            'total_successes': total_successes,
            'success_rate': f"{(total_successes / total_attempts * 100):.1f}%" if total_attempts > 0 else "N/A"
        }
        
        try:
            with open(self.training_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f"Saved session data ({successes}/{attempts} successful) to training file")
            
            FileRotationManager.check_and_rotate_file(self.training_file)
            
        except IOError as e:
            logging.error(f"Failed to write training data: {e}")
    
    def _update_accuracy_log(self, attempts, successes):
        if os.path.exists(self.accuracy_file):
            try:
                with open(self.accuracy_file, 'r') as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError()
            except (json.JSONDecodeError, ValueError):
                data = {'total_attempts': 0, 'total_successes': 0, 'sessions': []}
        else:
            data = {'total_attempts': 0, 'total_successes': 0, 'sessions': []}
        
        data['total_attempts'] += attempts
        data['total_successes'] += successes
        data['last_updated'] = datetime.now().isoformat()
        
        data['sessions'].append({
            'timestamp': datetime.now().isoformat(),
            'attempts': attempts,
            'successes': successes
        })
        
        cutoff = datetime.now() - timedelta(days=90)
        data['sessions'] = [
            s for s in data['sessions']
            if datetime.fromisoformat(s['timestamp']) > cutoff
        ]
        
        try:
            with open(self.accuracy_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f"Updated accuracy log: {data['total_successes']}/{data['total_attempts']} total")
        except IOError as e:
            logging.error(f"Failed to write accuracy log: {e}")
    
    def _update_health_tracking(self):
        if not self.health_file:
            return
        
        if os.path.exists(self.health_file):
            try:
                with open(self.health_file, 'r') as f:
                    health = json.load(f)
                # Ensure required keys exist (handles old/corrupted files)
                if 'methods' not in health:
                    health['methods'] = {}
                if 'total_sessions' not in health:
                    health['total_sessions'] = 0
            except (json.JSONDecodeError, IOError):
                health = self._init_health_structure()
        else:
            health = self._init_health_structure()
        
        health['total_sessions'] += 1
        health['last_updated'] = datetime.now().isoformat()
        
        for match in self.session_data:
            if not match.get('success'):
                continue
            
            method = match.get('match_method', 'unknown')
            score = match.get('match_score', 0)
            
            if method not in health['methods']:
                health['methods'][method] = {
                    'uses': 0,
                    'avg_score': 0,
                    'min_score': 1.0,
                    'max_score': 0.0
                }
            
            stats = health['methods'][method]
            stats['uses'] += 1
            old_avg = stats['avg_score']
            stats['avg_score'] = ((old_avg * (stats['uses'] - 1)) + score) / stats['uses']
            stats['min_score'] = min(stats['min_score'], score)
            stats['max_score'] = max(stats['max_score'], score)
        
        try:
            with open(self.health_file, 'w') as f:
                json.dump(health, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to write health tracking: {e}")
    
    def _init_health_structure(self):
        return {
            'total_sessions': 0,
            'last_updated': datetime.now().isoformat(),
            'methods': {}
        }
