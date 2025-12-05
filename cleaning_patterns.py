"""
Centralized cleaning patterns for movie/TV show name processing.

This module provides a single source of truth for all patterns used in
name cleaning, including quality tags, codecs, audio formats, etc.

Release group names are loaded from cleaning_data.json (gitignored) to
keep potentially sensitive names out of the repository.
"""

import json
import re
from pathlib import Path
from typing import List, Set, Optional

# ============================================================================
# RESOLUTION & QUALITY PATTERNS
# ============================================================================

RESOLUTIONS = {
    '2160p', '1080p', '720p', '480p', '4k', 'uhd',
}

HDR_FORMATS = {
    'hdr', 'hdr10', 'hdr10+', 'dolby vision', 'dv',
}

# ============================================================================
# VIDEO SOURCE PATTERNS  
# ============================================================================

VIDEO_SOURCES = {
    'webrip', 'web-dl', 'webdl', 'hdtv', 
    'bluray', 'blu-ray', 'bdrip', 'brrip', 'hdrip',
    'remux', 'dvdrip', 'dvdscr',
    'cam', 'ts', 'tc', 'hdcam', 'hdts',
    'screener', 'scr', 'r5', 'dvdr',
    # Container formats (sometimes appear in folder names)
    'mp4', 'mkv', 'avi', 'mov', 'wmv',
}

# ============================================================================
# VIDEO CODEC PATTERNS
# ============================================================================

VIDEO_CODECS = {
    'x264', 'x265', 'h264', 'h265', 'h.264', 'h.265',
    'hevc', 'avc', 'xvid', 'divx', 'mpeg', 'mpeg2',
    'vp9', 'av1',
}

# ============================================================================
# AUDIO CODEC & FORMAT PATTERNS
# ============================================================================

AUDIO_CODECS = {
    'aac', 'aac2', 'ac3', 'eac3', 'mp3', 'flac', 'opus',
    'dts', 'dts-hd', 'dtshd', 'truehd', 'atmos',
}

AUDIO_CHANNELS = {
    '5.1', '7.1', '2.0', '1.0',
}

# ============================================================================
# EDITION & RELEASE TYPE PATTERNS
# ============================================================================

EDITION_TAGS = {
    'extended', 'unrated', 'remastered', 'directors cut', 'final cut',
    'ultimate edition', 'special edition', 'theatrical cut', 'theatrical',
    'criterion collection', 'complete series', 'complete',
    'proper', 'repack', 'rerip', 'sample',
    'limited', 'internal',
}

# ============================================================================
# LANGUAGE TAGS
# ============================================================================

LANGUAGE_TAGS = {
    'english', 'eng', 'french', 'german', 'spanish', 'italian',
    'russian', 'japanese', 'korean', 'hindi', 'chinese',
    'dan', 'ita', 'fra', 'deu', 'esp', 'rus', 'jpn', 'kor',
    'en-sub', 'eng-sub', 'english-sub', 'subs', 'sub',
    'multi', 'dual audio', 'dubbed',
}

# ============================================================================
# GENRE DESCRIPTOR TAGS (often appended to filenames, not part of title)
# ============================================================================

GENRE_TAGS = {
    'film noir', 'sci-fi', 'scifi', 'documentary', 'doc',
    'anime', 'animated', 'animation',
}

# ============================================================================
# STREAMING SERVICE TAGS
# ============================================================================

STREAMING_SERVICES = {
    'nf', 'netflix', 'amzn', 'amazon', 'hulu', 'dsnp', 'disney+',
    'hmax', 'hbo', 'atvp', 'apple tv+', 'pcok', 'peacock',
    'paramount+', 'crav', 'stan',
}

# ============================================================================
# COMPILED REGEX PATTERNS
# ============================================================================

def _build_word_pattern(words: Set[str]) -> str:
    """Build a regex pattern that matches any of the words as whole words."""
    # Escape special regex characters and join with |
    escaped = [re.escape(w).replace(r'\ ', r'[-\s]*') for w in sorted(words, key=len, reverse=True)]
    return r'\b(?:' + '|'.join(escaped) + r')\b'


# Pre-built patterns for common use
RESOLUTION_PATTERN = _build_word_pattern(RESOLUTIONS)
HDR_PATTERN = _build_word_pattern(HDR_FORMATS)
VIDEO_SOURCE_PATTERN = _build_word_pattern(VIDEO_SOURCES)
VIDEO_CODEC_PATTERN = _build_word_pattern(VIDEO_CODECS)
AUDIO_CODEC_PATTERN = _build_word_pattern(AUDIO_CODECS)
AUDIO_CHANNEL_PATTERN = r'\b(?:5\.1|7\.1|2\.0|1\.0)\b'
EDITION_PATTERN = _build_word_pattern(EDITION_TAGS)
LANGUAGE_PATTERN = _build_word_pattern(LANGUAGE_TAGS)
STREAMING_PATTERN = _build_word_pattern(STREAMING_SERVICES)
GENRE_PATTERN = _build_word_pattern(GENRE_TAGS)


# Combined quality patterns (order matters - more specific first)
# These include special regex patterns for complex cases beyond simple word matching
QUALITY_SOURCE_PATTERNS = [
    RESOLUTION_PATTERN,
    HDR_PATTERN,
    VIDEO_SOURCE_PATTERN,
    # Video codecs with flexible spacing/punctuation (H.264, H 264, H264)
    r'\b(?:x264|x265|hevc|h\.?\s*264|h\.?\s*265|avc|xvid|divx)\b',
    # Audio codecs with flexible numbering (AAC, AAC2, AAC2 0, AAC2.0)
    r'\b(?:aac\d*(?:[\s.]*\d+)?|ac3|dts(?:-?hd)?|truehd|atmos|mp3|eac3|flac)\b',
    AUDIO_CHANNEL_PATTERN,
    STREAMING_PATTERN,
    GENRE_PATTERN,
    # Release groups and common tags (loaded from centralized list + common ones)
    r'\b(?:rarbg|vppv|yts|yify|evo|etrg|fgp|ano|sujaidr|amzn|subs|ntb)\b',
    r'\b(?:mulvacoded|en-sub|eng-sub|english-sub|ime)\b',
    r'\b(?:h\d{3})\b',  # H264, H265, etc. as numbers
    r'\b(?:ddp\d+\.?\d*)\b',  # DDP5.1, DDP2.0
]

EDITION_PATTERNS = [
    EDITION_PATTERN,
]

# ============================================================================
# RELEASE GROUP HANDLING (loaded from external file)
# ============================================================================

_release_groups: Optional[Set[str]] = None
_cleaning_data_path = Path(__file__).parent / 'cleaning_data.json'


def _load_cleaning_data() -> dict:
    """Load cleaning data from JSON file."""
    if _cleaning_data_path.exists():
        try:
            with open(_cleaning_data_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_cleaning_data(data: dict) -> None:
    """Save cleaning data to JSON file."""
    with open(_cleaning_data_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, sort_keys=True)


def get_release_groups() -> Set[str]:
    """Get the set of known release group names (loaded from cleaning_data.json)."""
    global _release_groups
    if _release_groups is None:
        data = _load_cleaning_data()
        _release_groups = set(data.get('release_groups', []))
    return _release_groups


def add_release_group(name: str) -> None:
    """Add a release group name to the known list."""
    global _release_groups
    groups = get_release_groups()
    normalized = name.lower().strip()
    if normalized and normalized not in groups:
        groups.add(normalized)
        data = _load_cleaning_data()
        data['release_groups'] = sorted(groups)
        _save_cleaning_data(data)


def get_release_group_pattern() -> str:
    """Get regex pattern for known release groups."""
    groups = get_release_groups()
    if not groups:
        return r'(?!)'  # Never matches if no groups defined
    escaped = [re.escape(g) for g in sorted(groups, key=len, reverse=True)]
    return r'\b(?:' + '|'.join(escaped) + r')\b'


# ============================================================================
# FORBIDDEN MARKERS (for bracket content detection)
# ============================================================================

def get_forbidden_markers() -> List[str]:
    """
    Get list of regex patterns for forbidden markers.
    These are used to detect quality/release info inside brackets.
    """
    markers = []
    
    # Add all the static patterns
    for pattern_set in [RESOLUTIONS, VIDEO_SOURCES, VIDEO_CODECS, AUDIO_CODECS, EDITION_TAGS]:
        for item in pattern_set:
            markers.append(re.escape(item).replace(r'\ ', r'[-\s]*'))
    
    # Add HDR patterns
    markers.extend(['hdr10?', r'dolby\s*vision'])
    
    # Add audio channels
    markers.extend([r'5\.1', r'7\.1'])
    
    # Add release groups
    for group in get_release_groups():
        markers.append(re.escape(group))
    
    # Add subs pattern
    markers.append(r'subs?')
    
    return markers


def get_forbidden_union_pattern() -> str:
    """Get combined regex pattern for all forbidden markers."""
    markers = get_forbidden_markers()
    return r'(?:' + '|'.join(markers) + r')'


# ============================================================================
# HELPER FUNCTIONS FOR COMMON CLEANING OPERATIONS
# ============================================================================

def remove_quality_tags(text: str) -> str:
    """Remove all quality/source/codec tags from text."""
    for pattern in QUALITY_SOURCE_PATTERNS:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def remove_edition_tags(text: str) -> str:
    """Remove edition/release type tags from text."""
    for pattern in EDITION_PATTERNS:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def remove_language_tags(text: str) -> str:
    """Remove language tags from text."""
    text = re.sub(LANGUAGE_PATTERN, ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def remove_release_groups(text: str) -> str:
    """Remove known release group names from text."""
    pattern = get_release_group_pattern()
    text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def remove_website_prefixes(text: str) -> str:
    """Remove website prefixes like 'www.site.com - ' or '[ www.site.com ] - '."""
    # Bracketed website prefixes
    text = re.sub(r'^\s*\[\s*www\.[^\]]+\]\s*-\s*', '', text, flags=re.IGNORECASE)
    # Non-bracketed website prefixes
    text = re.sub(r'^\s*www\.[^\s]+\s+-\s*', '', text, flags=re.IGNORECASE)
    # .Com markers
    text = re.sub(r'^.*?\.Com[._\s]+', '', text, flags=re.IGNORECASE)
    return text.strip()


def remove_brackets_with_forbidden_content(text: str) -> str:
    """Remove bracketed content that contains quality/release markers."""
    forbidden_pattern = get_forbidden_union_pattern()
    
    # Check each bracketed section
    def should_remove(match):
        inner = match.group(1)
        if re.search(forbidden_pattern, inner, flags=re.IGNORECASE):
            return ' '
        return match.group(0)
    
    # Process different bracket types
    text = re.sub(r'\[([^\]]*)\]', should_remove, text)
    text = re.sub(r'\(([^)]*)\)', should_remove, text)
    
    return re.sub(r'\s+', ' ', text).strip()


def normalize_separators(text: str) -> str:
    """Convert dots and underscores to spaces, normalize whitespace."""
    text = re.sub(r'[._]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' \t.-_')


def clean_release_group_suffix(text: str) -> str:
    """
    Remove release group suffixes from end of text.
    Handles: dash-prefixed groups, all-lowercase groups, alphanumeric groups.
    """
    # All-lowercase 4+ char words at end (like "moviesbyrizzo")
    text = re.sub(r'\s+\b[a-z]{4,15}\b\s*$', ' ', text)
    # Alphanumeric with digits at end (like "Retic1337")
    text = re.sub(r'\s+\b[A-Za-z]+\d+[A-Za-z0-9]*\b\s*$', ' ', text)
    # Dash-prefixed groups at end (require space before dash to preserve hyphenated words like "A-Team")
    text = re.sub(r'\s-\s*\b[A-Za-z0-9]{2,15}\b\s*$', ' ', text)
    return text.strip()


def remove_season_episode_patterns(text: str) -> str:
    """Remove season/episode patterns like S01, Season 1, S01E01."""
    text = re.sub(r'\bS\d+(?:-\d+)?\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\bSeason\s*\d+(?:-\d+)?\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\bS\d+E\d+\b', ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def clean_folder_name(name: str) -> str:
    """
    Standard cleaning applied to folder names when extracting show/movie names.
    
    This is the unified function for cleaning parent/grandparent folder names
    to extract the show or movie name.
    """
    # Remove website prefixes first
    name = remove_website_prefixes(name)
    
    # Remove quality and edition tags (before normalizing to catch patterns with dots)
    name = remove_quality_tags(name)
    name = remove_edition_tags(name)
    
    # Remove specific patterns that might have dots
    name = re.sub(r'\b(?:NF|WEBRip|WEB-DL|DDP\d+\.?\d*)\b', ' ', name, flags=re.IGNORECASE)
    
    # Remove standalone decimal numbers (quality tag fragments like "2.0")
    name = re.sub(r'\b\d+\.\d+\b', ' ', name)
    
    # Normalize separators
    name = normalize_separators(name)
    
    # Remove bracketed and parenthesized content
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\([^)]*\)', '', name)
    
    # Remove season/episode patterns
    name = remove_season_episode_patterns(name)
    
    # Remove language tags
    name = remove_language_tags(name)
    
    # Remove release group suffixes
    name = clean_release_group_suffix(name)
    
    # Remove known release groups
    name = remove_release_groups(name)
    
    # Clean up empty parentheses and stray punctuation
    name = re.sub(r'\(\s*\)', ' ', name)
    name = re.sub(r'[–—\-]{2,}', ' ', name)
    name = re.sub(r'[–—\-]+\s*$', ' ', name)
    name = re.sub(r'^\s*[–—\-]+', ' ', name)
    
    # Final cleanup
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name


# ============================================================================
# INITIALIZATION - Create default cleaning_data.json if it doesn't exist
# ============================================================================

def initialize_cleaning_data():
    """Create cleaning_data.json with default structure if it doesn't exist."""
    if not _cleaning_data_path.exists():
        default_data = {
            "release_groups": [],
            "_comment": "Add release group names here. This file is gitignored."
        }
        _save_cleaning_data(default_data)


# Initialize on import
initialize_cleaning_data()

