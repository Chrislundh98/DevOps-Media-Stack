"""
Last-resort translation-based matching.

When all cascade strategies fail, takes the top-scoring candidates
(including those near-missed due to year/language mismatch) and attempts
to identify foreign-language alternate title matches.

Uses:
  - langdetect      (fast language ID, ~1 MB, pure Python)
  - argostranslate  (offline neural translation, ~40 MB per language pair,
                     downloaded on first use and cached at ~/.local/share/argos-translate)

Runs ONLY when explicitly called — no background threads.
Designed for CPU-only (i5-class): model inference is fast (<1 s per sentence).
"""

import logging
import re

logger = logging.getLogger('translation_matcher')

# Languages we will attempt to translate to English
TRANSLATABLE = {
    'de', 'fr', 'it', 'es', 'nl', 'sv', 'da', 'no', 'fi',
    'ko', 'ja', 'zh-cn', 'zh-tw',
    'pt', 'pl', 'ru', 'hu', 'cs', 'ro', 'sk',
}

# Strip codec / resolution / year suffixes to isolate the title
_TITLE_STRIP_RE = re.compile(
    r'[\._]'
    r'|\b(19|20)\d{2}\b.*'
    r'|\b(1080p|2160p|720p|4k|uhd|bluray|blu.ray|web|hdtv|dvd|complete'
    r'|dual|remux|hevc|x265|x264|avc|hdr|sdr|dts|aac|flac|truehd|atmos'
    r'|dolby|digital)\b.*',
    re.IGNORECASE,
)

def _extract_title_part(name: str) -> str:
    """Strip codec/res/year suffixes and return the title portion."""
    s = _TITLE_STRIP_RE.sub(' ', name)
    return re.sub(r'\s{2,}', ' ', s).strip()

def _detect_lang(text: str) -> str:
    """Return ISO-639-1 lang code, or 'en' on failure / unsupported."""
    try:
        from langdetect import detect, LangDetectException
        lang = detect(text)
        return lang if lang in TRANSLATABLE else 'en'
    except Exception:
        return 'en'

def _ensure_argos_model(from_lang: str) -> bool:
    """Download and install the from_lang→en argostranslate package if missing."""
    try:
        import argostranslate.package as ap
        installed = {(p.from_code, p.to_code) for p in ap.get_installed_packages()}
        if (from_lang, 'en') in installed:
            return True

        logger.info(f"[translation] Downloading {from_lang}→en model (~40 MB, one-time)...")
        ap.update_package_index()
        available = ap.get_available_packages()
        pkg = next((p for p in available
                    if p.from_code == from_lang and p.to_code == 'en'), None)
        if not pkg:
            logger.warning(f"[translation] No argostranslate package for {from_lang}→en")
            return False

        ap.install_from_path(pkg.download())
        logger.info(f"[translation] {from_lang}→en model ready")
        return True

    except ImportError:
        logger.debug("[translation] argostranslate not installed")
        return False
    except Exception as e:
        logger.warning(f"[translation] Model install failed ({from_lang}→en): {e}")
        return False

def _translate_to_english(text: str, from_lang: str) -> str | None:
    """Translate text to English via argostranslate. Returns None on failure."""
    if not _ensure_argos_model(from_lang):
        return None
    try:
        import argostranslate.translate as at
        translation = at.get_translation_from_codes(from_lang, 'en')
        if translation:
            return translation.translate(text)
        return None
    except Exception as e:
        logger.debug(f"[translation] translate({from_lang}→en) failed: {e}")
        return None

def _token_overlap(a: str, b: str) -> float:
    """Jaccard token overlap (lowercased alpha tokens only)."""
    ta = set(re.findall(r'[a-z]{2,}', a.lower()))
    tb = set(re.findall(r'[a-z]{2,}', b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def find_translation_match(tracker_name: str,
                           tracker_core: str,
                           candidates,
                           compare_sizes_fn,
                           tracker_size_mb=None):
    """
    Attempt to match tracker_name against candidates that may have foreign-language titles.

    Args:
        tracker_name:    Raw tracker torrent name (English).
        tracker_core:    Normalised core title extracted from tracker_name.
        candidates:      List of (torrent_obj, size_mb) pairs.
        compare_sizes_fn: MLMatcher.compare_sizes bound method.
        tracker_size_mb: Tracker file size in MB (optional).

    Returns:
        (torrent | None, score, 'translation_match' | None)
    """
    if not candidates or not tracker_core:
        return None, 0.0, None

    tracker_core_lower = tracker_core.lower().strip()

    # Size pre-filter (±15%) to keep the pool small
    if tracker_size_mb and tracker_size_mb > 0:
        size_ok = [
            (qt, sz) for (qt, sz) in candidates
            if compare_sizes_fn(tracker_size_mb, sz, tolerance_percent=15.0)[0]
        ]
        pool = size_ok if size_ok else candidates
    else:
        pool = candidates

    best_torrent = None
    best_score = 0.0

    for (qt, qsize) in pool:
        raw_title = _extract_title_part(qt.name)
        if not raw_title:
            continue

        lang = _detect_lang(raw_title)

        if lang == 'en':
            # Straight overlap as a safety net (shouldn't normally match here)
            overlap = _token_overlap(tracker_core_lower, raw_title)
            if overlap >= 0.60 and overlap > best_score:
                best_score = overlap
                best_torrent = qt
            continue

        translated = _translate_to_english(raw_title, lang)
        if not translated:
            continue

        logger.info(
            f"[translation] '{qt.name[:55]}' ({lang}) → '{translated[:60]}'"
        )

        translated_lower = translated.lower()

        # Strong signal: tracker core title is a prefix of the translated title
        # e.g. "shell" → "Shell Beauty has its price"
        if translated_lower.startswith(tracker_core_lower):
            score = 0.85
            if tracker_size_mb and qsize:
                ok, _, _ = compare_sizes_fn(tracker_size_mb, qsize, tolerance_percent=15.0)
                if ok:
                    score = 0.92
            if score > best_score:
                best_score = score
                best_torrent = qt
            continue

        # Weaker signal: meaningful token overlap after translation
        overlap = _token_overlap(tracker_core_lower, translated)
        if overlap >= 0.50 and overlap > best_score:
            best_score = overlap
            best_torrent = qt

    if best_torrent:
        logger.info(
            f"[translation] '{tracker_name[:55]}' → "
            f"'{best_torrent.name[:55]}' (score={best_score:.3f})"
        )
        return best_torrent, best_score, 'translation_match'

    return None, 0.0, None
