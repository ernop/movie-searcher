"""GuessIt filename parsing shared by scanning, backfills and episode consumers."""
from functools import lru_cache
from pathlib import PurePosixPath
import re

from guessit import guessit


def _values(value):
    return value if isinstance(value, list) else [value] if value is not None else []


@lru_cache(maxsize=32768)
def parsed_name(name):
    return guessit(str(name).replace('\\', '/'), {'advanced': True})


def property_value(parsed, name):
    values = list(dict.fromkeys(m.value for m in _values(parsed.get(name))))
    return values[0] if len(values) == 1 else values or None


def episode_numbers(name):
    parsed = parsed_name(name)
    seasons = _values(parsed.get('season'))
    episodes = _values(parsed.get('episode'))
    if len(seasons) != 1 or not episodes:
        return None
    season = seasons[0]
    # GuessIt's weak compact-number guesses include film years (1918 -> 19/18).
    if 'weak-episode' in season.tags or not 0 <= season.value <= 100:
        return None
    if any('weak-episode' in e.tags for e in episodes):
        return None
    # A single digit after x also describes aspect ratios, e.g. 4x3 or 16x9.
    if 'x' in season.initiator.raw.casefold() and (any(len(e.raw) < 2 for e in episodes) or any(c.isspace() for c in season.initiator.raw)):
        return None
    numbers = sorted({e.value for e in episodes})
    return (season.value, numbers) if all(0 <= n <= 9999 for n in numbers) else None


def file_identity(path):
    """Return parser evidence; no network calls or invented episode numbering."""
    path = str(path).replace('\\', '/')
    base = parsed_name(PurePosixPath(path).name)
    parent = parsed_name(PurePosixPath(path).parent.name)
    full = parsed_name('/'.join(PurePosixPath(path).parts[-2:]))
    title = property_value(base, 'title')
    if not isinstance(title, str) or title.isdecimal():
        title = property_value(full, 'title')
    season_context = any('weak-episode' not in m.tags and 0 <= m.value <= 100
                         for m in _values(parent.get('season')))
    if season_context and any('weak-episode' in m.tags for m in _values(base.get('episode'))):
        folder_titles = [m.value for m in _values(full.get('title')) if 'filepart-title' in m.tags]
        if len(folder_titles) == 1:
            title = folder_titles[0]
    year = property_value(base, 'year') or property_value(full, 'year')
    # Explicit Episode N filenames without a season are TV evidence, but do not
    # establish season numbering. A dated film can also use Episode in its title.
    dated_name = re.search(r'(?<!\d)(?:18|19|20)\d{2}(?!\d)', PurePosixPath(path).name)
    episode_context = not year and not dated_name and any(
        'weak-episode' not in m.tags and m.initiator.raw.casefold().startswith('episode')
        for m in _values(base.get('episode')))
    return {'contextual_tv': bool(episode_context or season_context and full.get('episode')), 'title': title if isinstance(title, str) else None,
            'year': year if isinstance(year, int) else None,
            'country': property_value(base, 'country') or property_value(full, 'country'),
            'episodes': episode_numbers(PurePosixPath(path).name)}


def is_extra(path):
    # These are file roles, not episode parsing rules. Extras never prove ownership.
    words = str(path).replace('\\', '/').casefold().replace('_', ' ').replace('.', ' ')
    return any(term in words for term in ('deleted scene', 'featurette', 'behind the scenes',
                                         'bonus features', '/extras/', '/extra/', ' - extras/', 'gag reel',
                                         'outtakes', 'bloopers', 'season extra'))
