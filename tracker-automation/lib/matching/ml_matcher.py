"""
MLMatcher — multi-strategy cascade matcher.

Strategy order (first confident hit wins):
  1. Exact normalized-name match (always tried first)
  2. Size-first pre-filter → ML score (best for large UHD/Blu-ray releases)
  3. Release-group exact targeting → ML score
  4. Core title + year → size verify (catches generic qBit folder names)
  5. Full ML scan of all candidates (no size filter)
  6. Legacy fuzzy fallback (if ML not loaded)
  7. Failure diagnosis — records WHY every strategy failed for ML training

Every call is logged to training data.  Failed matches include a detailed
diagnosis dict so the ML can learn from the structure of failures.
"""
import logging
from datetime import datetime
from .normalizer import NameNormalizer
from .features import extract_features
from .ml_model import MatchModel
from .training import TrainingDataManager
from .translation_matcher import find_translation_match

logger = logging.getLogger('ml_matcher')

CONFIDENCE_HIGH   = 0.85
CONFIDENCE_MEDIUM = 0.55
CONFIDENCE_LOW    = 0.35

# Size-filter tolerance bands
SIZE_TIGHT_PCT   = 3.0   # ±3% for large files
SIZE_NORMAL_PCT  = 7.0   # ±7% for medium files
SIZE_LOOSE_PCT   = 15.0  # ±15% for small files / fallback
SIZE_LOOSE_MB    = 200   # always accept within 200 MB regardless of %

class MLMatcher:

    def __init__(self, model_dir, training_data_dir, json_dir,
                 training_file=None, accuracy_file=None, health_file=None):
        self.normalizer = NameNormalizer()
        self.ml = MatchModel(model_dir, training_data_dir, json_dir)

        if training_file and accuracy_file:
            self.training_manager = TrainingDataManager(
                training_file, accuracy_file, health_file)
        else:
            self.training_manager = None

        self._session_predictions = []

    # Size helpers

    def compare_sizes(self, size1_mb, size2_mb,
                      tolerance_percent=7.0, tolerance_mb=SIZE_LOOSE_MB):
        if size1_mb is None or size2_mb is None or size1_mb <= 0 or size2_mb <= 0:
            return False, None, None
        size_diff_mb = abs(size1_mb - size2_mb)
        max_size = max(size1_mb, size2_mb)
        size_diff_percent = (size_diff_mb / max_size) * 100

        # Tighter tolerance for very large files (>50 GB)
        if max_size > 51200:
            eff = SIZE_TIGHT_PCT
        elif max_size > 20480:
            eff = 4.0
        elif max_size > 10240:
            eff = 5.0
        else:
            eff = tolerance_percent

        pct_tol = max_size * (eff / 100)
        return (size_diff_mb <= pct_tol or size_diff_mb <= tolerance_mb,
                size_diff_mb, size_diff_percent)

    def _size_filter(self, candidates_with_sizes, tracker_size_mb, tolerance_pct=None):
        """
        Return subset of (torrent, size_mb) pairs whose size is within tolerance
        of tracker_size_mb.  Returns ALL if tracker size unknown.
        """
        if not tracker_size_mb or tracker_size_mb <= 0:
            return candidates_with_sizes

        # Choose tolerance based on file size
        if tolerance_pct is None:
            if tracker_size_mb > 51200:
                tolerance_pct = SIZE_TIGHT_PCT
            elif tracker_size_mb > 20480:
                tolerance_pct = 5.0
            else:
                tolerance_pct = SIZE_NORMAL_PCT

        result = []
        for (qt, size_mb) in candidates_with_sizes:
            ok, _, _ = self.compare_sizes(tracker_size_mb, size_mb,
                                          tolerance_percent=tolerance_pct)
            if ok:
                result.append((qt, size_mb))
        return result

    # Veto helpers

    def _check_season_veto(self, t_meta, q_meta):
        t_season = t_meta.get('season')
        q_season = q_meta.get('season')
        if not t_season or not q_season:
            return False
        if t_meta.get('is_complete_pack') or q_meta.get('is_complete_pack'):
            if t_meta.get('season_end'):
                try:
                    if int(t_season) <= int(q_season) <= int(t_meta['season_end']):
                        return False
                except ValueError:
                    pass
            return False
        return t_season != q_season

    def _check_release_group_veto(self, t_meta, q_meta):
        t_group = t_meta.get('release_group')
        q_group = q_meta.get('release_group')
        if not t_group or not q_group:
            return False
        if t_group == q_group:
            return False
        if t_group.startswith(q_group) or q_group.startswith(t_group):
            return False
        return True

    def _check_year_veto(self, t_meta, q_meta):
        t_year = t_meta.get('year')
        q_year = q_meta.get('year')
        if not t_year or not q_year:
            return False
        # Allow ±1 year — different regions / release calendars often differ by one year
        try:
            return abs(int(t_year) - int(q_year)) > 1
        except (ValueError, TypeError):
            return t_year != q_year

    def _hard_vetoed(self, t_meta, q_meta, disable_year_veto=False):
        if self._check_season_veto(t_meta, q_meta):
            return True
        if not disable_year_veto and self._check_year_veto(t_meta, q_meta):
            return True
        return False

    def _vetoed(self, t_meta, q_meta, disable_rg_veto=False, disable_year_veto=False):
        if self._hard_vetoed(t_meta, q_meta, disable_year_veto=disable_year_veto):
            return True
        if not disable_rg_veto and self._check_release_group_veto(t_meta, q_meta):
            return True
        return False

    # ML scoring helper

    def _score_candidates(self, tracker_name, candidates, tracker_tag,
                          tracker_size_mb):
        """
        Run ML prediction on a list of (torrent, size_mb) pairs.
        Returns list of dicts sorted by probability descending.
        """
        if not self.ml.is_ready():
            return []

        scored = []
        for (qt, qbit_size_mb) in candidates:
            try:
                features = extract_features(
                    tracker_name, qt.name, tracker_tag,
                    tracker_size_mb, qbit_size_mb,
                )
            except Exception as e:
                logger.debug(f"Feature extraction error for '{qt.name[:40]}': {e}")
                continue

            prob = self.ml.predict(features)
            if prob is not None:
                scored.append({
                    'torrent': qt,
                    'probability': prob,
                    'features': features,
                    'qbit_size_mb': qbit_size_mb,
                })

        scored.sort(key=lambda c: c['probability'], reverse=True)
        return scored

    def _pick_from_scored(self, scored, tracker_size_mb, method_prefix,
                          c_high=None, c_medium=None):
        """
        Apply confidence thresholds to a sorted scored list.
        Returns (torrent, prob, method) or (None, 0, None).
        c_high / c_medium override the module-level defaults for relaxed retries.
        """
        if not scored:
            return None, 0.0, None

        high   = c_high   if c_high   is not None else CONFIDENCE_HIGH
        medium = c_medium if c_medium is not None else CONFIDENCE_MEDIUM

        best = scored[0]
        prob = best['probability']

        if prob >= high:
            return best['torrent'], prob, f'{method_prefix}_high'

        if prob >= medium:
            return best['torrent'], prob, f'{method_prefix}_medium'

        if prob >= CONFIDENCE_LOW:
            # Need size confirmation for low-confidence hits
            if tracker_size_mb and best['qbit_size_mb']:
                size_ok, _, _ = self.compare_sizes(tracker_size_mb,
                                                   best['qbit_size_mb'])
                if size_ok:
                    return best['torrent'], prob, f'{method_prefix}_low_size_ok'
            return None, prob, None

        return None, prob, None

    # Core matching cascade

    def find_best_match(self, tracker_name, qbit_torrents, tracker_tag="TL",
                        tracker_size_mb=None, confidence_high=None,
                        confidence_medium=None, disable_rg_veto=False,
                        disable_year_veto=False):
        """
        Multi-strategy cascade.  Returns (torrent | None, score, method_str).
        method_str on failure encodes why: e.g. 'no_match:size_prefilter_0_candidates'

        Optional relaxation params (used by RetryQueue / RecoveryQueue):
          confidence_high    — override CONFIDENCE_HIGH (default 0.85)
          confidence_medium  — override CONFIDENCE_MEDIUM (default 0.55)
          disable_rg_veto    — ignore release-group mismatch veto
          disable_year_veto  — ignore year mismatch veto
        """
        # Aliases for cleaner call-sites below
        c_high = confidence_high
        c_medium = confidence_medium

        norm_tracker = self.normalizer.normalize(tracker_name, tracker_tag)
        _, t_meta = self.normalizer.normalize(tracker_name, tracker_tag,
                                              return_metadata=True)
        t_tokens = self.normalizer.extract_tokens(tracker_name, tracker_tag)
        t_group = t_meta.get('release_group', '')

        # ── Strategy 1: exact normalized match ─────────────────────────
        # Run before the no_tokens guard: numeric-only titles like "31 (2016)"
        # produce zero tokens but still have a valid normalized form to compare.
        for qt in qbit_torrents:
            norm_qbit = self.normalizer.normalize(qt.name, tracker_tag)
            _, q_meta = self.normalizer.normalize(qt.name, tracker_tag,
                                                  return_metadata=True)
            if norm_tracker == norm_qbit:
                if self._vetoed(t_meta, q_meta,
                                disable_rg_veto=disable_rg_veto,
                                disable_year_veto=disable_year_veto):
                    continue
                self._log_match(tracker_name, qt.name, 'exact', 1.0, True,
                                tracker_size_mb)
                self._record_prediction(tracker_name, qt, 1.0, 'exact',
                                        tracker_size_mb,
                                        getattr(qt, 'size', 0) / (1024*1024))
                return qt, 1.0, 'exact'

        if not t_tokens:
            logger.warning(f"No tokens from: {tracker_name}")
            self._log_match(tracker_name, None, None, 0.0, False,
                            tracker_size_mb,
                            diagnosis={'reason': 'no_tokens_extracted'})
            return None, 0, 'no_match:no_tokens'

        # ── Prepare candidate list with sizes ──────────────────────────
        all_candidates = []
        # Candidates dropped only by year mismatch — passed to translation strategy
        year_vetoed_candidates = []
        for qt in qbit_torrents:
            _, q_meta = self.normalizer.normalize(qt.name, tracker_tag,
                                                  return_metadata=True)
            if self._hard_vetoed(t_meta, q_meta,
                                 disable_year_veto=disable_year_veto):
                # Collect if the ONLY hard veto is year (not season)
                if (not self._check_season_veto(t_meta, q_meta) and
                        self._check_year_veto(t_meta, q_meta)):
                    qsize = getattr(qt, 'size', 0) / (1024 * 1024)
                    year_vetoed_candidates.append((qt, qsize))
                continue
            qsize = getattr(qt, 'size', 0) / (1024 * 1024)
            all_candidates.append((qt, qsize))

        if not self.ml.is_ready():
            logger.warning("ML model not loaded, falling back to legacy")
            return self._legacy_fallback(tracker_name, qbit_torrents,
                                         tracker_tag, tracker_size_mb,
                                         t_meta, t_tokens, norm_tracker)

        diagnosis = {
            'total_candidates': len(all_candidates),
            'strategies_tried': [],
        }

        # ── Strategy 2: size-first pre-filter → ML ────────────────────
        if tracker_size_mb and tracker_size_mb > 0:
            size_filtered = self._size_filter(all_candidates, tracker_size_mb)
            diagnosis['strategies_tried'].append({
                'name': 'size_prefilter_ml',
                'candidates_before': len(all_candidates),
                'candidates_after': len(size_filtered),
            })
            if size_filtered:
                scored = self._score_candidates(
                    tracker_name, size_filtered, tracker_tag, tracker_size_mb)
                result, prob, method = self._pick_from_scored(
                    scored, tracker_size_mb, 'size_ml',
                    c_high=c_high, c_medium=c_medium)
                if result:
                    logger.info(
                        f"[size_ml] '{tracker_name[:50]}' → "
                        f"'{result.name[:50]}' ({prob:.3f})"
                    )
                    self._log_match(tracker_name, result.name, method,
                                    prob, True, tracker_size_mb,
                                    diagnosis=diagnosis)
                    self._record_prediction(tracker_name, result, prob,
                                            method, tracker_size_mb,
                                            scored[0]['qbit_size_mb'] if scored else None,
                                            runner_up=scored[1]['probability'] if len(scored) > 1 else None)
                    return result, prob, method

                # Log best failure candidate for diagnosis
                if scored:
                    diagnosis['strategies_tried'][-1]['best_score'] = scored[0]['probability']
                    diagnosis['strategies_tried'][-1]['best_candidate'] = scored[0]['torrent'].name[:60]
            else:
                diagnosis['strategies_tried'][-1]['note'] = 'no_size_matches'

        # ── Strategy 3: release-group targeting → ML ──────────────────
        if t_group:
            rg_filtered = []
            for (qt, qsize) in all_candidates:
                _, q_meta = self.normalizer.normalize(qt.name, tracker_tag,
                                                      return_metadata=True)
                q_group = q_meta.get('release_group', '')
                if q_group and (q_group == t_group or
                                q_group.startswith(t_group) or
                                t_group.startswith(q_group)):
                    rg_filtered.append((qt, qsize))

            diagnosis['strategies_tried'].append({
                'name': 'release_group_ml',
                'release_group': t_group,
                'candidates': len(rg_filtered),
            })

            if rg_filtered:
                scored = self._score_candidates(
                    tracker_name, rg_filtered, tracker_tag, tracker_size_mb)
                result, prob, method = self._pick_from_scored(
                    scored, tracker_size_mb, 'rg_ml',
                    c_high=c_high, c_medium=c_medium)
                if result:
                    logger.info(
                        f"[rg_ml] '{tracker_name[:50]}' → "
                        f"'{result.name[:50]}' ({prob:.3f})"
                    )
                    self._log_match(tracker_name, result.name, method,
                                    prob, True, tracker_size_mb,
                                    diagnosis=diagnosis)
                    self._record_prediction(tracker_name, result, prob,
                                            method, tracker_size_mb,
                                            scored[0]['qbit_size_mb'] if scored else None,
                                            runner_up=scored[1]['probability'] if len(scored) > 1 else None)
                    return result, prob, method

                if scored:
                    diagnosis['strategies_tried'][-1]['best_score'] = scored[0]['probability']

        # ── Strategy 4: core title + year → size verify ───────────────
        t_core = self.normalizer.extract_core_title(tracker_name, tracker_tag)
        t_year = t_meta.get('year', '')
        core_hits = []
        for (qt, qsize) in all_candidates:
            q_core = self.normalizer.extract_core_title(qt.name, tracker_tag)
            _, q_meta = self.normalizer.normalize(qt.name, tracker_tag,
                                                  return_metadata=True)
            q_year = q_meta.get('year', '')
            if t_core and q_core and t_core == q_core:
                if not t_year or not q_year or t_year == q_year:
                    core_hits.append((qt, qsize))

        diagnosis['strategies_tried'].append({
            'name': 'core_title_year',
            'core': t_core,
            'year': t_year,
            'hits': len(core_hits),
        })

        if core_hits and tracker_size_mb:
            for (qt, qsize) in core_hits:
                size_ok, _, _ = self.compare_sizes(tracker_size_mb, qsize)
                if size_ok:
                    logger.info(
                        f"[core_title] '{tracker_name[:50]}' → "
                        f"'{qt.name[:50]}' (core+year+size)"
                    )
                    self._log_match(tracker_name, qt.name, 'core_title_size',
                                    0.92, True, tracker_size_mb,
                                    diagnosis=diagnosis)
                    self._record_prediction(tracker_name, qt, 0.92,
                                            'core_title_size', tracker_size_mb,
                                            qsize)
                    return qt, 0.92, 'core_title_size'

        # ── Strategy 5: full ML scan (no size filter) ─────────────────
        scored_all = self._score_candidates(
            tracker_name, all_candidates, tracker_tag, tracker_size_mb)
        diagnosis['strategies_tried'].append({
            'name': 'full_ml_scan',
            'candidates': len(all_candidates),
            'best_score': scored_all[0]['probability'] if scored_all else None,
            'best_candidate': scored_all[0]['torrent'].name[:60] if scored_all else None,
        })

        result, prob, method = self._pick_from_scored(
            scored_all, tracker_size_mb, 'ml',
            c_high=c_high, c_medium=c_medium)
        if result:
            logger.info(
                f"[full_ml] '{tracker_name[:50]}' → "
                f"'{result.name[:50]}' ({prob:.3f})"
            )
            self._log_match(tracker_name, result.name, method,
                            prob, True, tracker_size_mb, diagnosis=diagnosis)
            self._record_prediction(tracker_name, result, prob, method,
                                    tracker_size_mb,
                                    scored_all[0]['qbit_size_mb'] if scored_all else None,
                                    runner_up=scored_all[1]['probability'] if len(scored_all) > 1 else None)
            return result, prob, method

        # ── Strategy 6: loose size filter with lowered ML threshold ────
        if tracker_size_mb and tracker_size_mb > 0:
            loose_filtered = self._size_filter(
                all_candidates, tracker_size_mb, tolerance_pct=SIZE_LOOSE_PCT)
            if loose_filtered and len(loose_filtered) < len(all_candidates):
                scored_loose = self._score_candidates(
                    tracker_name, loose_filtered, tracker_tag, tracker_size_mb)
                if scored_loose:
                    best_loose = scored_loose[0]
                    # Accept medium-confidence with loose size gate
                    effective_medium = c_medium if c_medium is not None else CONFIDENCE_MEDIUM
                    if best_loose['probability'] >= effective_medium:
                        qt = best_loose['torrent']
                        prob = best_loose['probability']
                        logger.info(
                            f"[loose_size_ml] '{tracker_name[:50]}' → "
                            f"'{qt.name[:50]}' ({prob:.3f})"
                        )
                        self._log_match(tracker_name, qt.name,
                                        'loose_size_ml', prob, True,
                                        tracker_size_mb, diagnosis=diagnosis)
                        self._record_prediction(
                            tracker_name, qt, prob, 'loose_size_ml',
                            tracker_size_mb, best_loose['qbit_size_mb'])
                        return qt, prob, 'loose_size_ml'

        # ── Strategy 7: translation-based matching (last resort) ───────
        # Build the translation pool: low-scoring ML candidates + year-vetoed ones
        translation_pool = []
        if scored_all:
            # Include all below-threshold candidates (up to top 10)
            translation_pool.extend(
                (c['torrent'], c['qbit_size_mb']) for c in scored_all[:10]
            )
        translation_pool.extend(year_vetoed_candidates)

        if translation_pool:
            t_core = self.normalizer.extract_core_title(tracker_name, tracker_tag)
            diagnosis['strategies_tried'].append({
                'name': 'translation_match',
                'pool_size': len(translation_pool),
                'tracker_core': t_core,
            })
            result, prob, method = find_translation_match(
                tracker_name, t_core,
                translation_pool, self.compare_sizes,
                tracker_size_mb,
            )
            if result:
                logger.info(
                    f"[translation] '{tracker_name[:50]}' → "
                    f"'{result.name[:50]}' ({prob:.3f})"
                )
                self._log_match(tracker_name, result.name, 'translation_match',
                                prob, True, tracker_size_mb, diagnosis=diagnosis)
                self._record_prediction(tracker_name, result, prob,
                                        'translation_match', tracker_size_mb,
                                        getattr(result, 'size', 0) / (1024 * 1024))
                return result, prob, 'translation_match'

        # ── No match found — record rich failure diagnosis ─────────────
        best_overall = scored_all[0] if scored_all else None
        failure_reason = self._diagnose_failure(
            tracker_name, tracker_tag, t_meta, t_tokens, t_group,
            all_candidates, best_overall, tracker_size_mb, diagnosis,
        )
        logger.warning(
            f"No match: '{tracker_name[:60]}' — {failure_reason}"
        )
        self._log_match(tracker_name,
                        best_overall['torrent'].name if best_overall else None,
                        None,
                        best_overall['probability'] if best_overall else 0.0,
                        False, tracker_size_mb, diagnosis=diagnosis)
        return None, 0, f'no_match:{failure_reason}'

    # Legacy fallback (when ML not loaded)

    def _legacy_fallback(self, tracker_name, qbit_torrents, tracker_tag,
                         tracker_size_mb, t_meta, t_tokens, norm_tracker):
        best_match = None
        best_score = 0.0

        for qt in qbit_torrents:
            _, q_meta = self.normalizer.normalize(qt.name, tracker_tag,
                                                  return_metadata=True)
            if self._vetoed(t_meta, q_meta):
                continue
            q_tokens = self.normalizer.extract_tokens(qt.name, tracker_tag)
            t_set = set(t_tokens)
            q_set = set(q_tokens)
            union = t_set | q_set
            score = len(t_set & q_set) / len(union) if union else 0.0

            if tracker_size_mb and hasattr(qt, 'size'):
                qsz = qt.size / (1024 * 1024)
                ok, _, _ = self.compare_sizes(tracker_size_mb, qsz)
                if ok:
                    score *= 1.1

            if score > best_score:
                best_score = score
                best_match = qt

        if best_match and best_score >= 0.75:
            self._log_match(tracker_name, best_match.name, 'legacy_fuzzy',
                            best_score, True, tracker_size_mb)
            return best_match, best_score, 'legacy_fuzzy'

        self._log_match(tracker_name, None, None, best_score, False,
                        tracker_size_mb,
                        diagnosis={'reason': 'legacy_fuzzy_below_threshold',
                                   'best_score': best_score})
        return None, 0, 'no_match:legacy_below_threshold'

    # Failure diagnosis

    def _diagnose_failure(self, tracker_name, tracker_tag, t_meta, t_tokens,
                          t_group, all_candidates, best_overall,
                          tracker_size_mb, diagnosis):
        """Return a short string describing the most likely reason for no match."""
        if not all_candidates:
            return 'no_candidates_after_veto'

        if not best_overall:
            return 'ml_no_predictions'

        best_prob = best_overall['probability']
        best_name = best_overall['torrent'].name

        # Did size mismatch kill all close candidates?
        if tracker_size_mb:
            for (qt, qsize) in all_candidates:
                ok, _, _ = self.compare_sizes(tracker_size_mb, qsize,
                                              tolerance_percent=SIZE_LOOSE_PCT)
                if ok:
                    break
            else:
                return f'all_candidates_size_mismatch(best={best_prob:.3f})'

        # Were tokens too sparse?
        if len(t_tokens) <= 2:
            return (f'sparse_tokens({len(t_tokens)})_best_prob={best_prob:.3f}'
                    f'_best_candidate={best_name[:40]}')

        if best_prob < CONFIDENCE_LOW:
            return (f'best_ml_prob_too_low({best_prob:.3f})'
                    f'_best={best_name[:40]}')

        return (f'below_threshold({best_prob:.3f})'
                f'_best={best_name[:40]}')

    # Logging helpers

    def _log_match(self, tracker_name, qbit_name, method, score, success,
                   tracker_size_mb, diagnosis=None):
        if not self.training_manager:
            return
        entry = {
            'tracker_name': tracker_name,
            'qbit_name': qbit_name,
            'match_method': method,
            'match_score': score,
            'success': success,
        }
        if tracker_size_mb:
            entry['tracker_size_mb'] = tracker_size_mb
        if diagnosis:
            entry['diagnosis'] = diagnosis
        self.training_manager.add_match_attempt(entry)

    def _record_prediction(self, tracker_name, qt, prob, method,
                           tracker_size_mb, qbit_size_mb, runner_up=None):
        self._session_predictions.append({
            'tracker_name': tracker_name,
            'qbit_name': qt.name,
            'qbit_hash': getattr(qt, 'hash', None),
            'probability': prob,
            'method': method,
            'timestamp': datetime.now().isoformat(),
            'tracker_size_mb': tracker_size_mb,
            'qbit_size_mb': qbit_size_mb,
            'runner_up_prob': runner_up,
        })

    def save_match_data(self):
        if self.training_manager:
            self.training_manager.save()
        self.ml.check_and_retrain()

    def get_session_predictions(self):
        return list(self._session_predictions)

    def clear_session_predictions(self):
        self._session_predictions.clear()
