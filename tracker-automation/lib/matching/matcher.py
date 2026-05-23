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
    
    # ---- SAFETY VETOES ----
    
    def _check_season_veto(self, tracker_meta, qbit_meta):
        """Hard veto if seasons don't match. Returns True if vetoed."""
        t_season = tracker_meta.get('season')
        q_season = qbit_meta.get('season')
        
        if not t_season or not q_season:
            return False
        
        # Complete packs can match any season within their range
        if tracker_meta.get('is_complete_pack'):
            season_end = tracker_meta.get('season_end')
            if season_end:
                try:
                    if int(t_season) <= int(q_season) <= int(season_end):
                        return False
                except ValueError:
                    pass
            return False  # Complete pack, be lenient
        
        if qbit_meta.get('is_complete_pack'):
            return False
        
        if t_season != q_season:
            logger.debug(f"Season veto: tracker S{t_season} vs qbit S{q_season}")
            return True
        
        return False
    
    def _check_release_group_veto(self, tracker_meta, qbit_meta):
        """Hard veto if release groups clearly differ. Returns True if vetoed."""
        t_group = tracker_meta.get('release_group')
        q_group = qbit_meta.get('release_group')
        
        if not t_group or not q_group:
            return False
        
        if t_group == q_group:
            return False
        
        # Allow close matches (e.g. flux vs flux2)
        if t_group.startswith(q_group) or q_group.startswith(t_group):
            return False
        
        logger.debug(f"Release group veto: '{t_group}' vs '{q_group}'")
        return True
    
    def _check_year_veto(self, tracker_meta, qbit_meta):
        """Hard veto if years differ (prevents sequel confusion). Returns True if vetoed."""
        t_year = tracker_meta.get('year')
        q_year = qbit_meta.get('year')
        
        if not t_year or not q_year:
            return False
        
        if t_year != q_year:
            logger.debug(f"Year veto: {t_year} vs {q_year}")
            return True
        
        return False
    
    def _all_vetoes_pass(self, tracker_meta, qbit_meta):
        """Run all safety vetoes. Returns True if all pass (no vetoes triggered)."""
        if self._check_season_veto(tracker_meta, qbit_meta):
            return False
        if self._check_release_group_veto(tracker_meta, qbit_meta):
            return False
        if self._check_year_veto(tracker_meta, qbit_meta):
            return False
        return True
    
    # ---- SCORING ----
    
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
    
    def _strong_evidence_check(self, tracker_tokens, qbit_tokens, tracker_meta, qbit_meta, size_match):
        """
        Check for strong evidence that two torrents are the same despite low fuzzy score.
        Handles cases like generic qBit folder names ("Hot Ones" matching "Hot Ones S23 ...").
        Returns a boost value (0.0 to 0.3).
        """
        boost = 0.0
        
        if not tracker_tokens or not qbit_tokens:
            return 0.0
        
        qbit_set = set(qbit_tokens)
        tracker_set = set(tracker_tokens)
        
        if len(qbit_set) == 0:
            return 0.0
        
        overlap = qbit_set & tracker_set
        overlap_ratio = len(overlap) / len(qbit_set)
        
        # If qbit tokens are a subset of tracker tokens (common with generic folder names)
        if overlap_ratio >= 0.8 and len(overlap) >= 2:
            boost += 0.15
            if size_match:
                boost += 0.10
        
        # AKA variant matching
        tracker_aka = tracker_meta.get('aka_variants', [])
        if tracker_aka:
            for aka in tracker_aka:
                aka_tokens = NameNormalizer.extract_tokens(aka, tracker_meta.get('tracker_tag', 'TL'))
                aka_overlap = set(aka_tokens) & qbit_set
                if len(aka_overlap) >= 2:
                    boost += 0.15
                    break
        
        return min(0.30, boost)
    
    # ---- MAIN MATCHING ----
    
    def find_best_match(self, tracker_name, qbit_torrents, tracker_tag="TL", tracker_size_mb=None):
        norm_tracker = self.normalizer.normalize(tracker_name, tracker_tag)
        tracker_tokens = self.normalizer.extract_tokens(tracker_name, tracker_tag)
        _, tracker_meta = self.normalizer.normalize(tracker_name, tracker_tag, return_metadata=True)
        
        if not tracker_tokens:
            logger.warning(f"No tokens extracted from tracker name: {tracker_name}")
            return None, 0, 'no_tokens'
        
        best_match = None
        best_score = 0
        best_method = None
        best_evidence_boost = 0
        best_size_match = False
        
        candidates = []
        
        for qbit_torrent in qbit_torrents:
            qbit_name = qbit_torrent.name
            norm_qbit = self.normalizer.normalize(qbit_name, tracker_tag)
            _, qbit_meta = self.normalizer.normalize(qbit_name, tracker_tag, return_metadata=True)
            
            # --- EXACT MATCH (still check vetoes) ---
            if norm_tracker == norm_qbit:
                if self._check_release_group_veto(tracker_meta, qbit_meta):
                    logger.info(f"Exact match vetoed by release group: {tracker_name} vs {qbit_name}")
                    continue
                if self._check_season_veto(tracker_meta, qbit_meta):
                    logger.info(f"Exact match vetoed by season: {tracker_name} vs {qbit_name}")
                    continue
                if self._check_year_veto(tracker_meta, qbit_meta):
                    logger.info(f"Exact match vetoed by year: {tracker_name} vs {qbit_name}")
                    continue
                
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
            
            # --- SAFETY VETOES ---
            if not self._all_vetoes_pass(tracker_meta, qbit_meta):
                continue
            
            # --- FUZZY SCORING ---
            qbit_tokens = self.normalizer.extract_tokens(qbit_name, tracker_tag)
            fuzzy_score = self.fuzzy_ratio(tracker_tokens, qbit_tokens)
            
            # Size validation
            size_match = False
            if tracker_size_mb and hasattr(qbit_torrent, 'size'):
                qbit_size_mb = qbit_torrent.size / (1024 * 1024)
                size_match, size_diff, _ = self.compare_sizes(tracker_size_mb, qbit_size_mb)
                
                if size_match:
                    fuzzy_score *= 1.1
            
            # Strong evidence boost (helps with generic folder names like "Hot Ones")
            evidence_boost = self._strong_evidence_check(
                tracker_tokens, qbit_tokens, tracker_meta, qbit_meta, size_match
            )
            fuzzy_score += evidence_boost
            
            candidates.append({
                'torrent': qbit_torrent,
                'score': fuzzy_score,
                'method': 'fuzzy',
                'size_match': size_match,
                'evidence_boost': evidence_boost
            })
            
            if fuzzy_score > best_score:
                best_score = fuzzy_score
                best_match = qbit_torrent
                best_method = 'fuzzy'
                best_evidence_boost = evidence_boost
                best_size_match = size_match
        
        # Dynamic threshold: lower when strong evidence + size match
        effective_threshold = self.match_threshold
        if best_evidence_boost >= 0.15 and best_size_match:
            effective_threshold = 0.60
        
        if best_score >= effective_threshold:
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
        
        # --- SUBSTRING FALLBACK (with vetoes) ---
        for qbit_torrent in qbit_torrents:
            _, qbit_meta = self.normalizer.normalize(qbit_torrent.name, tracker_tag, return_metadata=True)
            
            if not self._all_vetoes_pass(tracker_meta, qbit_meta):
                continue
            
            qbit_tokens = self.normalizer.extract_tokens(qbit_torrent.name, tracker_tag)
            sub_score = self.substring_score(tracker_tokens, qbit_tokens)
            
            if sub_score >= 0.8:
                if tracker_size_mb and hasattr(qbit_torrent, 'size'):
                    qbit_size_mb = qbit_torrent.size / (1024 * 1024)
                    size_match, _, _ = self.compare_sizes(tracker_size_mb, qbit_size_mb)
                    
                    if size_match:
                        match_data = {
                            'tracker_name': tracker_name,
                            'qbit_name': qbit_torrent.name,
                            'match_method': 'substring',
                            'match_score': sub_score,
                            'success': True
                        }
                        if self.training_manager:
                            self.training_manager.add_match_attempt(match_data)
                        return qbit_torrent, sub_score, 'substring'
        
        # --- NO MATCH ---
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