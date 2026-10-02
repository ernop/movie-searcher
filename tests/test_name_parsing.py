"""Release years in scene names: the last year before the tags, a bracketed year first."""
import pytest

from scanning import clean_movie_name

EMPTY = {'exact_strings': set(), 'bracket_patterns': [], 'parentheses_patterns': [], 'year_patterns': True}


@pytest.mark.parametrize('path, expected', [
    ('/m/incoming/1900-1976-bb8e5720/1900 (1976) [BluRay] [1080p] [YTS.LT]/1900.1976.1080p.BluRay.x264-[YTS.LT].mp4', ('1900', 1976)),
    ('/m/Class.Of.1999.1990.1080p.BluRay.x264.AAC-[YTS.MX].mp4', ('Class of 1999', 1990)),
    ('/m/Death.Race.2000.1975.720p.BluRay.999MB.HQ.x265.10bit-GalaxyRG.mkv', ('Death Race 2000', 1975)),
    ('/m/x/1918 1985 DVDRip XviD.avi', ('1918', 1985)),
    ('/m/Sin City (2005) IMDB 8.0/EXTENDED.1080p.BluRay.x265-RARBG.mp4', ('Sin City', 2005)),
    ("/m/1918 - Charlie Chaplin - A Dog's Life.avi", ("Charlie Chaplin - A Dog's Life", 1918)),
    ('/m/YouTube/Play of the Week - Professional Foul (1977) by Tom Stoppard & Michael Lindsay-Hogg (2024) [D5MYDrWGobg].mp4',
     ('Play of the Week - Professional Foul', 1977)),
    ('/m/WWII/02 Distant War (September 1939 to May 1940).mp4', ('02 Distant War', 1939)),
    ('/m/1408.2007.1080p.BluRay.x264.YIFY.mp4', ('1408', 2007)),
    ('/m/Backdraft 1991 DvDrip[Eng]-greenbud1969.avi', ('Backdraft', 1991)),
])
def test_release_year_and_title(path, expected):
    assert tuple(clean_movie_name(path, EMPTY)[:2]) == expected


def test_years_agree():
    from media_identity import years_agree
    assert years_agree(1992, 1993) and years_agree(None, 1931) and years_agree(1992, None)
    assert not years_agree(1992, 1931)
    assert years_agree(2009, 2011, '/m/incoming/Rango-2011-415ec089/Rango (2009)/Rango.mp4')
