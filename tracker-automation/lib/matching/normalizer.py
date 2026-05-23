import re
import logging

class NameNormalizer:
    
    UNICODE_MAP = {
        'æ': 'ae', 'ø': 'o', 'å': 'a', 'ö': 'o', 'ü': 'u', 'ä': 'a',
        'ñ': 'n', 'ç': 'c', 'ß': 'ss',
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2026': '...',
    }
    
    AUDIO_PATTERNS = [
        # Channel configs: 5.1, 7.1, 2.0 — dot or space separated
        r'\b(dd5[\. ]?1|dd2[\. ]?0|7[\. ]1|5[\. ]1|2[\. ]0|stereo|mono)\b',
        # Codec identifiers — DTS-HD MA appears with hyphens OR spaces
        r'\b(dts[-. ]?hd[-. ]?ma|dts[-. ]?hd|dts[-. ]?x|truehd|atmos|ddp|dd\+|ac-?3|eac3|lpcm)\b',
        r'\b(pcm|aac|opus|flac)\b',
        # Bare "ma" after dts-hd is already stripped; also strip bare "dts" suffix
        r'\b(dts)\b',
    ]
    
    QUALITY_PATTERNS = [
        r'\b(1080p|720p|480p|360p|2160p|4k|uhd)\b',
        r'\b(\d{3,4}p)\b',
        r'\b(sd|hd)\b'
    ]
    
    CODEC_PATTERNS = [
        r'\b(x264|h264|h\.264|x265|hevc|h265|h\.265)\b',
        r'\b(avc|vc-?1|xvid)\b'
    ]
    
    NOISE_PATTERNS = [
        (r'\b(bluray|blu-ray|blu\s?ray|br|uhd\s*bluray)\b', 'bluray'),
        (r'\b(web-dl|webrip|web|web\s?dl)\b', 'web'),
        (r'\b(hdtv|dvdrip|bdrip|blurayrip)\b', ' '),
        (r'\b(10bit|8bit|10-bit|8-bit)\b', ' '),
        (r'\b(remux|remuux)\b', 'remux'),
        (r'\b(hdr10\+?|hdr|sdr|hlg)\b', ' '),
        (r'\b(multi|multii?\d+|multilanguage)\b', ' '),
        (r'\b(uncut|unrated|extended|theatrical|directors\.?cut)\b', ' '),
        (r'\b(repack|proper|real\.proper|internal)\b', ' '),
        (r'\b(nf|amzn|dsnp|hulu|hmax|hbo|atvp)\b', ' '),
        (r'\b(subpack|subbed|dubbed|dual-audio|dual audio)\b', ' '),
        (r'\b(nordic|english|german|french|spanish|italian)\b', ' '),
        (r'\b(remastered|boxset|collection|drm-free)\b', ' '),
        (r'\b(dlc|dlcs|win|64bit|x64|x86)\b', ' '),
    ]
    
    STOPWORDS = {
        'and', 'the', 'of', 'in', 'to', 'a', 'an', 'is', 'at', 'for', 'on', 
        'with', 'as', 'by', 'from', 'it', 's', 'e', 'ep', 'episode', 'season',
        'pt', 'part', 'vol', 'volume'
    }
    
    # Technical noise tokens — not meaningful for title matching
    NOISE_TOKENS = {
        'web', 'bluray', 'remux', 'ddp5', 'ddp7', 'ddp2', 'dd5', 'dd2',
        '264', '265', 'avc', 'complete', 'shorts', '2ch',
        'dts', 'hdr', 'sdr', 'hlg', 'hdr10', 'uhd', 'flac', 'opus',
        'aac', 'pcm', 'lpcm', 'mkv', 'mp4', 'avi',
    }
    
    @staticmethod
    def remove_tracker_suffix(name, tracker_tag="TL"):
        if tracker_tag.upper() != "TL":
            return name
        
        name = re.sub(r'\.r\d{1,2}$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+rar\d{1,2}$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*-\s*rar\d{1,2}$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'([-][a-zA-Z0-9]{2,})FL$', r'\1', name)
        name = re.sub(r'([a-zA-Z0-9]{3,})FL$', r'\1', name)
        
        if name.endswith('-FL') or name.endswith(' FL'):
            name = name[:-3]
        
        return name
    
    @staticmethod
    def normalize(name, tracker_tag="TL", return_metadata=False):
        metadata = {
            'original': name,
            'tracker_tag': tracker_tag,
            'year': None,
            'season': None,
            'season_end': None,
            'episode': None,
            'round_number': None,
            'aka_variants': [],
            'repack_source': None,
            'release_group': None,
            'is_complete_pack': False,
            'removed_elements': []
        }
        
        normalized = re.sub(r'\s*\(\s*\d+[\.,]?\d*\s*(?:gi?b|mi?b|ti?b|kb)\s*\)\s*$', '', name, flags=re.IGNORECASE)
        
        normalized = NameNormalizer.remove_tracker_suffix(normalized, tracker_tag)
        
        normalized = re.sub(r'^\s*\[[a-zA-Z0-9\.-]+\]\s*', '', normalized, flags=re.IGNORECASE)
        
        aka_match = re.search(r'\bAKA\s+(.+?)(?:\s+\d{4}|\s*$)', normalized, flags=re.IGNORECASE)
        if aka_match:
            metadata['aka_variants'].append(aka_match.group(1).strip())
            normalized = re.sub(r'\s+AKA\s+[^0-9]+(?=\s+\d{4}|\s*$)', ' ', normalized, flags=re.IGNORECASE)
        
        repack_match = re.search(r'\[(FitGirl|DODI|KaOs|ElAmigos|GOG)(?:\s+Repack)?\]', normalized, flags=re.IGNORECASE)
        if repack_match:
            metadata['repack_source'] = repack_match.group(1).lower()
        
        # Detect COMPLETE packs (S01-S02, S01-S05, etc.)
        if re.search(r's\d{1,2}\s*-\s*s\d{1,2}', normalized, flags=re.IGNORECASE):
            metadata['is_complete_pack'] = True
        if re.search(r'\bcomplete\b', normalized, flags=re.IGNORECASE):
            metadata['is_complete_pack'] = True
        
        for char, replacement in NameNormalizer.UNICODE_MAP.items():
            normalized = normalized.replace(char, replacement)
        
        normalized = normalized.replace('_', ' ')
        normalized = re.sub(r"[''`]", '', normalized)
        normalized = normalized.lower()
        
        normalized = re.sub(r'\.(mkv|iso|ts|mp4|avi|torrent|7z|rar|zip|nsp|xci|exe)$', '', normalized, flags=re.IGNORECASE)
        
        round_match = re.search(r'round\s*(\d{1,2})', normalized, flags=re.IGNORECASE)
        if round_match:
            metadata['round_number'] = round_match.group(1)
            normalized = re.sub(r'round\s*(\d{1,2})', r'r\1', normalized, flags=re.IGNORECASE)
        
        normalized = re.sub(r'(\d{4})x(\d{1,2})', r'\1 r\2', normalized)
        
        # --- Extract season/episode BEFORE stripping ---
        # Multi-season range first: S01-S02
        multi_match = re.search(r's(\d{1,2})\s*-\s*s(\d{1,2})', normalized, flags=re.IGNORECASE)
        if multi_match:
            metadata['season'] = multi_match.group(1).lstrip('0') or '0'
            metadata['season_end'] = multi_match.group(2).lstrip('0') or '0'
        
        # Match "S01E02" pattern
        se_match = re.search(r's(\d{1,2})e(\d{1,2})', normalized, flags=re.IGNORECASE)
        if se_match:
            if not metadata['season']:
                metadata['season'] = se_match.group(1).lstrip('0') or '0'
            metadata['episode'] = se_match.group(2).lstrip('0') or '0'
        elif not metadata['season']:
            # Match "S01" pattern (season only)
            s_match = re.search(r'\bs(\d{1,2})\b', normalized, flags=re.IGNORECASE)
            if s_match:
                metadata['season'] = s_match.group(1).lstrip('0') or '0'
        
        # Match "Season XX" pattern (catches "Season 2", "Season 21", etc.)
        if not metadata['season']:
            season_word_match = re.search(r'season\s*(\d{1,2})', normalized, flags=re.IGNORECASE)
            if season_word_match:
                metadata['season'] = season_word_match.group(1).lstrip('0') or '0'
        
        # Episode only
        if not metadata['episode']:
            e_match = re.search(r'\be(\d{1,2})\b', normalized, flags=re.IGNORECASE)
            if e_match:
                metadata['episode'] = e_match.group(1).lstrip('0') or '0'
        
        # --- Strip audio, noise, quality, codecs ---
        for pattern in NameNormalizer.AUDIO_PATTERNS:
            normalized = re.sub(pattern, ' ', normalized, flags=re.IGNORECASE)
        
        for pattern, replacement in NameNormalizer.NOISE_PATTERNS:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        
        for pattern in NameNormalizer.QUALITY_PATTERNS:
            normalized = re.sub(pattern, ' ', normalized, flags=re.IGNORECASE)
        
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', normalized)
        if year_match:
            metadata['year'] = year_match.group(1)
            normalized = re.sub(r'\b(19\d{2}|20\d{2})\b', ' ', normalized)
        
        for pattern in NameNormalizer.CODEC_PATTERNS:
            normalized = re.sub(pattern, ' ', normalized, flags=re.IGNORECASE)
        
        # Strip season/episode markers from normalized string
        normalized = re.sub(r's\d{1,2}e\d{1,2}', ' ', normalized, flags=re.IGNORECASE)
        normalized = re.sub(r'\bs\d{1,2}\b', ' ', normalized, flags=re.IGNORECASE)
        normalized = re.sub(r'season\s*\d{1,2}', ' ', normalized, flags=re.IGNORECASE)
        normalized = re.sub(r'episode\s*\d{1,2}', ' ', normalized, flags=re.IGNORECASE)
        
        # Extract release group (last -group pattern)
        release_match = re.search(r'-([a-z0-9]+)$', normalized)
        if release_match:
            metadata['release_group'] = release_match.group(1)
            normalized = re.sub(r'-[a-z0-9]+$', '', normalized)
        
        normalized = re.sub(r'[^\w\s]', ' ', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        if return_metadata:
            return normalized, metadata
        return normalized
    
    @staticmethod
    def extract_tokens(name, tracker_tag="TL"):
        """Extract meaningful title tokens, filtering out technical noise."""
        normalized = NameNormalizer.normalize(name, tracker_tag)
        words = normalized.split()
        tokens = [
            w for w in words 
            if len(w) >= 3 
            and w not in NameNormalizer.STOPWORDS
            and w not in NameNormalizer.NOISE_TOKENS
            and not re.match(r'^s\d{1,2}$', w)       # strip s01, s02 etc
            and not re.match(r'^\d{1,3}$', w)         # strip bare numbers
        ]
        return tokens
    
    @staticmethod
    def extract_core_title(name, tracker_tag="TL"):
        normalized, metadata = NameNormalizer.normalize(name, tracker_tag, return_metadata=True)
        
        words = normalized.split()
        
        if metadata['year']:
            core_words = []
            for word in words:
                if word.isdigit() and len(word) == 4:
                    break
                core_words.append(word)
            if core_words:
                return ' '.join(core_words)
        
        if len(words) > 5:
            return ' '.join(words[:5])
        
        return normalized