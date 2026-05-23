import difflib
import re
from .normalizer import NameNormalizer

FEATURE_NAMES = [
    'token_jaccard',
    'token_overlap_min',
    'token_overlap_max',
    'char_similarity',
    'normalized_levenshtein',
    'size_ratio',
    'size_diff_mb',
    'has_size_data',
    'release_group_match',
    'year_match',
    'season_match',
    'episode_match',
    'token_count_ratio',
    'core_title_similarity',
    'longest_common_subseq_ratio',
    'prefix_match_len',
    'tracker_token_count',
    'qbit_token_count',
    'common_token_count',
    'unique_tracker_tokens',
    'unique_qbit_tokens',
    'qbit_subset_of_tracker',
    'tracker_subset_of_qbit',
    'digit_token_overlap',
    'alpha_token_overlap',
]

def _levenshtein(s1, s2):
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]

def _lcs_length(s1, s2):
    m, n = len(s1), len(s2)
    if m == 0 or n == 0:
        return 0
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]

def extract_features(tracker_name, qbit_name, tracker_tag="TL",
                     tracker_size_mb=None, qbit_size_mb=None):
    normalizer = NameNormalizer

    norm_tracker = normalizer.normalize(tracker_name, tracker_tag)
    norm_qbit = normalizer.normalize(qbit_name, tracker_tag)
    _, t_meta = normalizer.normalize(tracker_name, tracker_tag, return_metadata=True)
    _, q_meta = normalizer.normalize(qbit_name, tracker_tag, return_metadata=True)

    t_tokens = normalizer.extract_tokens(tracker_name, tracker_tag)
    q_tokens = normalizer.extract_tokens(qbit_name, tracker_tag)

    t_set = set(t_tokens)
    q_set = set(q_tokens)
    intersection = t_set & q_set
    union = t_set | q_set

    token_jaccard = len(intersection) / len(union) if union else 0.0
    token_overlap_min = len(intersection) / min(len(t_set), len(q_set)) if min(len(t_set), len(q_set)) > 0 else 0.0
    token_overlap_max = len(intersection) / max(len(t_set), len(q_set)) if max(len(t_set), len(q_set)) > 0 else 0.0

    char_similarity = difflib.SequenceMatcher(None, norm_tracker, norm_qbit).ratio()

    max_len = max(len(norm_tracker), len(norm_qbit))
    if max_len > 0:
        lev = _levenshtein(norm_tracker, norm_qbit)
        normalized_levenshtein = 1.0 - (lev / max_len)
    else:
        normalized_levenshtein = 0.0

    if tracker_size_mb and qbit_size_mb and tracker_size_mb > 0 and qbit_size_mb > 0:
        size_ratio = min(tracker_size_mb, qbit_size_mb) / max(tracker_size_mb, qbit_size_mb)
        size_diff_mb = abs(tracker_size_mb - qbit_size_mb)
        has_size_data = 1.0
    else:
        size_ratio = -1.0
        size_diff_mb = -1.0
        has_size_data = 0.0

    t_group = t_meta.get('release_group')
    q_group = q_meta.get('release_group')
    if t_group and q_group:
        if t_group == q_group:
            release_group_match = 1.0
        elif t_group.startswith(q_group) or q_group.startswith(t_group):
            release_group_match = 0.5
        else:
            release_group_match = 0.0
    else:
        release_group_match = -1.0

    t_year = t_meta.get('year')
    q_year = q_meta.get('year')
    if t_year and q_year:
        year_match = 1.0 if t_year == q_year else 0.0
    else:
        year_match = -1.0

    t_season = t_meta.get('season')
    q_season = q_meta.get('season')
    if t_season and q_season:
        season_match = 1.0 if t_season == q_season else 0.0
    else:
        season_match = -1.0

    t_ep = t_meta.get('episode')
    q_ep = q_meta.get('episode')
    if t_ep and q_ep:
        episode_match = 1.0 if t_ep == q_ep else 0.0
    else:
        episode_match = -1.0

    max_tokens = max(len(t_tokens), len(q_tokens))
    min_tokens = min(len(t_tokens), len(q_tokens))
    token_count_ratio = min_tokens / max_tokens if max_tokens > 0 else 0.0

    t_core = normalizer.extract_core_title(tracker_name, tracker_tag)
    q_core = normalizer.extract_core_title(qbit_name, tracker_tag)
    core_title_similarity = difflib.SequenceMatcher(None, t_core, q_core).ratio()

    max_str = max(len(norm_tracker), len(norm_qbit))
    if max_str > 0:
        lcs = _lcs_length(norm_tracker, norm_qbit)
        longest_common_subseq_ratio = lcs / max_str
    else:
        longest_common_subseq_ratio = 0.0

    prefix_len = 0
    for a, b in zip(norm_tracker, norm_qbit):
        if a == b:
            prefix_len += 1
        else:
            break

    t_digits = {t for t in t_tokens if t.isdigit()}
    q_digits = {t for t in q_tokens if t.isdigit()}
    t_alpha = t_set - t_digits
    q_alpha = q_set - q_digits
    digit_overlap = len(t_digits & q_digits) / len(t_digits | q_digits) if (t_digits | q_digits) else -1.0
    alpha_overlap = len(t_alpha & q_alpha) / len(t_alpha | q_alpha) if (t_alpha | q_alpha) else 0.0

    qbit_sub = 1.0 if q_set and q_set.issubset(t_set) else 0.0
    tracker_sub = 1.0 if t_set and t_set.issubset(q_set) else 0.0

    return [
        token_jaccard,
        token_overlap_min,
        token_overlap_max,
        char_similarity,
        normalized_levenshtein,
        size_ratio,
        size_diff_mb,
        has_size_data,
        release_group_match,
        year_match,
        season_match,
        episode_match,
        token_count_ratio,
        core_title_similarity,
        longest_common_subseq_ratio,
        float(prefix_len),
        float(len(t_tokens)),
        float(len(q_tokens)),
        float(len(intersection)),
        float(len(t_set - q_set)),
        float(len(q_set - t_set)),
        qbit_sub,
        tracker_sub,
        digit_overlap,
        alpha_overlap,
    ]
