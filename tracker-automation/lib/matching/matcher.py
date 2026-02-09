import logging
import difflib
from .normalizer import NameNormalizer
from .training import TrainingDataManager

logger = logging.getLogger('matching')

class TorrentMatcher:
    
    def __init__(self, match_threshold=0.75, training_file=None, accuracy_file=None, health_file=None):
        self.match_threshold = match_threshold
        self.normalizer = NameNormalizer()
        
        if training_file and accuracy_file:
            self.training_manager = TrainingDataManager(training_file, accuracy_file, health_file)
        else:
            self.training_manager = None
    
    def compare_sizes(self, size1_mb, size2_mb, tolerance_percent=5, tolerance_mb=100):
        if size1_mb is None or size2_mb is None or size1_mb <= 0 or size2_mb <= 0:
            return False, None, None
        
        size_diff_mb = abs(size1_mb - size2_mb)
        max_size = max(size1_mb, size2_mb)
        size_diff_percent = (size_diff_mb / max_size) * 100
        
        if max_size > 10240:
            if max_size > 51200:
                effective_percent = 1.5
            elif max_size > 20480:
                effective_percent = 2.0
            else:
                effective_percent = 3.0
            percent_tolerance_mb = max_size * (effective_percent / 100)
        else:
            percent_tolerance_mb = max_size * (tolerance_percent / 100)
        
        passes_percent = size_diff_mb <= percent_tolerance_mb
        passes_absolute = size_diff_mb <= tolerance_mb
        
        sizes_match = passes_percent or passes_absolute
        
        return sizes_match, size_diff_mb, size_diff_percent
    
    def fuzzy_ratio(self, terms1, terms2):
        if not terms1 or not terms2:
            return 0.0
        
        set1 = set(terms1)
        set2 = set(terms2)
        
        intersection = len(set1 & set2)
        
        partial_matches = 0
        matched_terms2 = set()
        
        for term1 in set1:
            if term1 in set2:
                continue
            
            for term2 in set2:
                if term2 in set1 or term2 in matched_terms2:
                    continue
                
                if len(term1) >= 3 and len(term2) >= 3:
                    if term1 in term2 or term2 in term1:
                        partial_matches += 0.7
                        matched_terms2.add(term2)
                        break
                    
                    min_len = min(len(term1), len(term2))
                    if min_len >= 4:
                        prefix_len = 0
                        for i in range(min_len):
                            if term1[i] == term2[i]:
                                prefix_len += 1
                            else:
                                break
                        
                        if prefix_len >= 4:
                            partial_matches += 0.5
                            matched_terms2.add(term2)
                            break
                    
                    ratio = difflib.SequenceMatcher(None, term1, term2).ratio()
                    if ratio >= 0.85:
                        partial_matches += ratio * 0.6
                        matched_terms2.add(term2)
                        break
        
        total_matches = intersection + partial_matches
        union = len(set1 | set2)
        
        if union == 0:
            return 0.0
        
        score = total_matches / union
        
        len_diff = abs(len(terms1) - len(terms2))
        if len_diff > 3:
            penalty = min(0.15, len_diff * 0.03)
            score = max(0, score - penalty)
        
        return min(1.0, score)
    
    def substring_score(self, terms1, terms2):
        if not terms1 or not terms2:
            return 0.0
        
        long_terms = terms1 if len(terms1) >= len(terms2) else terms2
        short_terms = terms2 if len(terms1) >= len(terms2) else terms1
        
        matched = 0
        for short_term in short_terms:
            for long_term in long_terms:
                if short_term in long_term or long_term in short_term:
                    matched += 1
                    break
        
        return matched / len(short_terms) if short_terms else 0.0
    
    def find_best_match(self, tracker_name, qbit_torrents, tracker_tag="TL", tracker_size_mb=None):
        norm_tracker = self.normalizer.normalize(tracker_name, tracker_tag)
        tracker_tokens = self.normalizer.extract_tokens(tracker_name, tracker_tag)
        
        if not tracker_tokens:
            logger.warning(f"No tokens extracted from tracker name: {tracker_name}")
            return None, 0, 'no_tokens'
        
        best_match = None
        best_score = 0
        best_method = None
        
        candidates = []
        
        for qbit_torrent in qbit_torrents:
            qbit_name = qbit_torrent.name
            norm_qbit = self.normalizer.normalize(qbit_name, tracker_tag)
            
            if norm_tracker == norm_qbit:
                match_data = {
                    'tracker_name': tracker_name,
                    'qbit_name': qbit_name,
                    'match_method': 'exact',
                    'match_score': 1.0,
                    'success': True
                }
                if self.training_manager:
                    self.training_manager.add_match_attempt(match_data)
                return qbit_torrent, 1.0, 'exact'
            
            qbit_tokens = self.normalizer.extract_tokens(qbit_name, tracker_tag)
            
            fuzzy_score = self.fuzzy_ratio(tracker_tokens, qbit_tokens)
            
            if tracker_size_mb and hasattr(qbit_torrent, 'size'):
                qbit_size_mb = qbit_torrent.size / (1024 * 1024)
                size_match, size_diff, _ = self.compare_sizes(tracker_size_mb, qbit_size_mb)
                
                if size_match:
                    fuzzy_score *= 1.1
            
            candidates.append({
                'torrent': qbit_torrent,
                'score': fuzzy_score,
                'method': 'fuzzy'
            })
            
            if fuzzy_score > best_score:
                best_score = fuzzy_score
                best_match = qbit_torrent
                best_method = 'fuzzy'
        
        if best_score >= self.match_threshold:
            match_data = {
                'tracker_name': tracker_name,
                'qbit_name': best_match.name,
                'match_method': best_method,
                'match_score': best_score,
                'success': True,
                'candidates_tested': len(candidates)
            }
            if self.training_manager:
                self.training_manager.add_match_attempt(match_data)
            return best_match, best_score, best_method
        
        for qbit_torrent in qbit_torrents:
            qbit_tokens = self.normalizer.extract_tokens(qbit_torrent.name, tracker_tag)
            substring_score = self.substring_score(tracker_tokens, qbit_tokens)
            
            if substring_score >= 0.8:
                if tracker_size_mb and hasattr(qbit_torrent, 'size'):
                    qbit_size_mb = qbit_torrent.size / (1024 * 1024)
                    size_match, _, _ = self.compare_sizes(tracker_size_mb, qbit_size_mb)
                    
                    if size_match:
                        match_data = {
                            'tracker_name': tracker_name,
                            'qbit_name': qbit_torrent.name,
                            'match_method': 'substring',
                            'match_score': substring_score,
                            'success': True
                        }
                        if self.training_manager:
                            self.training_manager.add_match_attempt(match_data)
                        return qbit_torrent, substring_score, 'substring'
        
        match_data = {
            'tracker_name': tracker_name,
            'qbit_name': None,
            'match_method': None,
            'match_score': best_score,
            'success': False,
            'candidates_tested': len(candidates),
            'best_candidate_score': best_score
        }
        if self.training_manager:
            self.training_manager.add_match_attempt(match_data)
        
        return None, 0, 'no_match'
    
    def save_match_data(self):
        if self.training_manager:
            self.training_manager.save()
