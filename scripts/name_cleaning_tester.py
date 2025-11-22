import sys
import json
from typing import List, Optional, Dict

# Ensure project root is on sys.path when running from scripts/
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanning import clean_movie_name, load_cleaning_patterns


# Edit this list: each case defines the raw input and the required, correct output.
# - expected_name is REQUIRED
# - expected_year is OPTIONAL (omit or set to None if not asserting year)
# Inputs should be full paths to test path-based cleaning
TEST_CASES: List[Dict[str, Optional[str]]] = [
    {
        "input": r"D:\movies\My fair lady.HDrip.avi",
        "expected_name": "My Fair Lady",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg).mkv",
        "expected_name": "Harold and Maude",
        "expected_year": 1971,
    },
    {
        "input": r"D:\movies\Spaceballs.1987.1080p.BluRay.x264.YIFY.mp4",
        "expected_name": "Spaceballs",
        "expected_year": 1987,
    },
    {
        "input": r"D:\movies\UHF.1989.1080p.BluRay.x264.YIFY.mp4",
        "expected_name": "UHF",
        "expected_year": 1989,
    },
    {
        "input": r"D:\movies\Kaiji\Season 1\[Triad]_Kaiji_-_12.mkv",
        "expected_name": "Kaiji S01E12",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\lavare-1980\L'avare (1980).mp4",
        "expected_name": "L'Avare",
        "expected_year": 1980,
    },
    {
        "input": r"D:\movies\L'ultima onda - The Last Wave (1977) 720p h264 Ac3 Ita Eng Sub Ita Eng - MIRCrew\L'ultima onda - The Last Wave (1977) 720p h264 Ac3 Ita Eng",
        "expected_name": "L'ultima onda - The Last Wave",
        "expected_year": 1977,
    },
    {
        "input": r"D:\movies\Babylon 5 (1993)\024 S02E01 Points of Departure.mkv",
        "expected_name": "Babylon 5 024 S02E01 Points of Departure",
        "expected_year": 1993,
    },
    {
        "input": r"D:\movies\BEASTARS.S01.JAPANESE.1080p.NF.WEBRip.DDP2.0.x264-AGLET[rartv]\BEASTARS.S01E10.A.Wolf.in.Sheeps.Clothing.1080p.NF.WEB-DL.DDP2.0.H.264-AGLET.mkv",
        "expected_name": "BEASTARS S01E10 A Wolf in Sheeps Clothing",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\The Undersea World of Jacques Cousteau [1971-5]\02-A Sound of Dolphins.mp4",
        "expected_name": "The Undersea World of Jacques Cousteau 02 A Sound of Dolphins",
        "expected_year": None, #because a removed item in the prior folder name doesn't mean that we lose the actaul filename
    },
    {
        "input": r"D:\movies\Love and Death (Woody Allen 1975) DivX DVDRip.avi",
        "expected_name": "Love and Death",
        "expected_year": 1975,
    },
    
    {
        "input": r"D:\movies\THE_BOYS_FROM_BRAZIL_Title1.mp4",
        "expected_name": "The Boys from Brazil", #title1 suffixes are bad text.  most movies _ actually is a space.
        "expected_year": None,
    },

    {
        "input": r"D:\movies\13 Assassins (2010) EXTENDED.720p.BRrip.sujaidr\13 Assassins (2010) EXTENDED.720p.BRrip.sujaidr.mkv",
        "expected_name": "13 Assassins",
        "expected_year": 2010,
    },
    {
        "input": r"D:\movies\720pMkv.Com_The.Baader.Meinhof.Complex.2008.720p.BluRay.x264.mp4",
        "expected_name": "The Baader Meinhof Complex",
        "expected_year": 2008,
    },
    {
        "input": r"D:\movies\2.or.3.Things.I.Know.About.Her.1967.BRRip.720p.x264-Classics\2.or.3.Things.I.Know.About.Her.1967.BRRip.720p.x264-Classics.mkv",
        "expected_name": "2 or 3 Things I Know about her",
        "expected_year": 1967,
    },
    {
        "input": r"D:\movies\The Twilight Zone (1959) Season 1-5 S01-05 (1080p BluRay x265 HEVC 10bit AAC 2.0 ImE)\Season 2\The Twilight Zone (1959) - S02E24 - The Rip Van Winkle Caper (1080p BluRay x265 ImE).mkv",
        "expected_name": "The Twilight Zone S02E24 The Rip Van Winkle Caper",
        "expected_year": 1959,
    },
    {
        "input": r"D:\movies\www.UIndex.org - Comedy Bang Bang S02E08 720p WEB-DL AAC2 0 H 264-NTb\Comedy Bang Bang S02E08 720p WEB-DL AAC2 0 H 264-NTb.mkv",
        "expected_name": "Comedy Bang Bang S02E08 ",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\The Wong Kar-Wai Quadrology [ 1990-2004 ] BluRay.720p-1080p.x264.anoXmous\03.In.The.Mood.For.Love.2000.Criteron.Collection.1080p.BluRay.x264.anoXmous\In.The.Mood.For.Love.2000.Criteron.Collection.1080p.BluRay.x264.anoXmous.mp4",
        "expected_name": "In the Mood for Love",
        "expected_year": 2000,
    },
    {
        "input": r"D:\movies\The.Birthday.Boys.S01.COMPLETE.720p.AMZN.WEBRip.x264-GalaxyTV[TGx]\The.Birthday.Boys.S01E01.720p.AMZN.WEBRip.x264-GalaxyTV.mkv",
        "expected_name": "The Birthday Boys S01E01 ",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\_done\Barbarella.1968.1080p.BluRay.x264-HD4U\Barbarella.1968.1080p.BluRay.x264-HD4U.mkv",
        "expected_name": "Barbarella",
        "expected_year": 1968,
    },
    {
        "input": r"D:\movies\A Perfect Spy (John le Carré) XviD moviesbyrizzo\A Perfect Spy (John le Carré) Vol1-Episode2.avi",
        "expected_name": "A Perfect Spy Vol1-Episode2",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\Forbrydelsen - Season 1 - 720p x265 HEVC - DAN-ITA (ENG SUBS) [BRSHNKV]\02. Tuesday November 4 .mp4",
        "expected_name": "Forbrydelsen S01E02 Tuesday November 4",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\Seinfeld.Complete.Series-720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded]\Season 8\Seinfeld.S08E16.The.Pothole.720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded].mkv",
        "expected_name": "Seinfeld S08E16 The Pothole",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\Seinfeld.Complete.Series-720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded]\Season 8\Seinfeld.S08E16.The.Pothole.720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded].mkv",
        "expected_name": "Seinfeld S08E16 The Pothole",
        "expected_year": None,
    },
    {
        "input": r"D:\movies\Seinfeld.Complete.Series-720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded]\Season 8\Seinfeld.S08E16.The.Pothole.720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded].mkv",
        "expected_name": "Seinfeld S08E16 The Pothole",
        "expected_year": None,
    },
    # {
    #     "input":""
    # }

    # {
    #     "input":""
    # }
    # Add more cases below. Examples:
    # {
    #     "input": "The.Matrix.1999.1080p.BluRay.x264.AC3.mkv",
    #     "expected_name": "The Matrix",
    #     "expected_year": 1999,
    # },
    # {
    #     "input": "Inception (2010) [1080p] [BluRay] [x265].mkv",
    #     "expected_name": "Inception",
    #     "expected_year": 2010,
    # },
    # {
    #     "input": "Seven Samurai 1954 720p.mp4",
    #     "expected_name": "Seven Samurai",
    #     "expected_year": 1954,
    # },
    # {
    #     "input": "Spirited Away (千と千尋の神隠し) (2001) 1080p.mkv",
    #     "expected_name": "Spirited Away",
    #     "expected_year": 2001,
    # },
]


def run_tests() -> int:
    patterns = load_cleaning_patterns()
    failures: List[Dict[str, object]] = []

    for idx, case in enumerate(TEST_CASES, 1):
        raw = case.get("input", "")
        expected_name = case.get("expected_name")
        expected_year = case.get("expected_year", None)

        cleaned_name, detected_year = clean_movie_name(raw, patterns)

        name_ok = (cleaned_name == expected_name)
        year_ok = True if expected_year is None else (detected_year == expected_year)

        if not (name_ok and year_ok):
            failures.append({
                "index": idx,
                "input": raw,
                "expected_name": expected_name,
                "actual_name": cleaned_name,
                "expected_year": expected_year,
                "actual_year": detected_year,
            })

    if failures:
        print("Name Cleaning Test Results: FAIL\n")
        for f in failures:
            print(f"[{f['index']}] Input: {f['input']}")
            print(f"  Expected name: {f['expected_name']}")
            print(f"  Actual name:   {f['actual_name']}")
            if f.get("expected_year") is not None:
                print(f"  Expected year: {f['expected_year']}")
                print(f"  Actual year:   {f['actual_year']}")
            print("")
        print(f"Total: {len(TEST_CASES)}  Failed: {len(failures)}  Passed: {len(TEST_CASES) - len(failures)}")
        return 1

    print("Name Cleaning Test Results: PASS")
    print(f"Total: {len(TEST_CASES)}  Failed: 0  Passed: {len(TEST_CASES)}")
    return 0


def debug_cleaning(input_path):
    """Debug the cleaning process for a specific input"""
    from scanning import clean_movie_name, load_cleaning_patterns
    import re
    from pathlib import Path

    patterns = load_cleaning_patterns()
    print(f"Input: {input_path}")

    # Replicate the initial steps from clean_movie_name
    original_name = input_path
    name = input_path
    year = None
    season = None
    episode = None

    # Check if input is a full path
    is_full_path = '/' in name or '\\' in name
    path_obj = None
    parent_folder = None

    if is_full_path:
        path_obj = Path(name)
        parent_folder = path_obj.parent.name if path_obj.parent.name else None
        name = path_obj.stem
        print(f"Filename: {name}")
        print(f"Parent: {parent_folder}")

    # STEP 0.5: Remove prefix markers
    name_orig = name
    name = re.sub(r'^.*?\.Com[._\s]+', '', name, flags=re.IGNORECASE)
    if name != name_orig:
        print(f"After Com_ removal: {name}")

    # STEP 1: Normalize separators
    name_orig = name
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' \t.-_')
    if name != name_orig:
        print(f"After normalization: {name}")

    # Extract year first
    if patterns.get('year_patterns', True):
        from scanning import extract_year_from_name
        year = extract_year_from_name(name)
        if year:
            year_pattern = rf'(?:[([{{<]\s*)?\b{year}\b\s*(?:[)\]}}>])?.*$'
            name_orig = name
            name = re.sub(year_pattern, '', name, count=1).strip()
            name = re.sub(r'\s*[\(\[\{<][^)\]}>]*$', '', name).strip()
            if name != name_orig:
                print(f"After year removal: {name}")

    # Extract episode info BEFORE removing tags
    if is_full_path and path_obj:
        # Extract season from parent folder
        parent_str = str(path_obj.parent.name) if path_obj.parent.name else str(path_obj.parent)
        season_match = re.search(r'(?:Season|season)\s*(\d+)', parent_str, re.IGNORECASE)
        if not season_match:
            season_match = re.search(r'\bS(\d+)\b', parent_str, re.IGNORECASE)
        if season_match:
            season = int(season_match.group(1))
            print(f"Season from parent: {season}")

        # Extract episode from filename
        original_filename = path_obj.stem
        sxxexx_match = re.search(r'\bS(\d+)E(\d+)\b', original_filename, re.IGNORECASE)
        if sxxexx_match:
            if season is None:
                season = int(sxxexx_match.group(1))
            episode = int(sxxexx_match.group(2))
            print(f"SxxExx match: S{season}E{episode}")
        else:
            print("No SxxExx match found")

    print(f"Final season: {season}, episode: {episode}")

    # Debug show name extraction
    if is_full_path and path_obj and (season is not None or episode is not None):
        has_episode_info = (season is not None or episode is not None)
        print(f"Has episode info: {has_episode_info}")

        if has_episode_info:
            parent_name = parent_folder
            print(f"Parent name: {parent_name}")

            if parent_name and not re.search(r'(?:Season|season)\s*\d+|^\s*S\d+\s*$', parent_name, re.IGNORECASE):
                print("Using parent as show name")
                show_name = parent_name
                print(f"Initial show_name: {show_name}")

                # Apply cleaning (simplified version)
                quality_source_patterns = [
                    r'\b(?:2160p|1080p|720p|480p|4k|uhd)\b',
                    r'\b(?:hdr|hdr10|dolby\s*vision|dv)\b',
                    r'\b(?:webrip|web[-\s]*dl|webdl|hdtv|bluray|blu[-\s]*ray|b[dr]rip|remux|dvdrip|cam|ts|tc)\b',
                    r'\b(?:x264|x265|hevc|h\.?264|h\.?265|avc)\b',
                    r'\b(?:aac|ac3|dts(?:-?hd)?|truehd|atmos|mp3|eac3)\b',
                    r'\b(?:5\.1|7\.1)\b',
                    r'\b(?:rarbg|vppv|yts|evo|etrg|fgp|ano|sujaidr)\b',
                    r'\b(?:h264)\b',
                ]
                edition_patterns = [
                    r'\b(?:proper|repack|rerip)\b',
                    r'\b(?:extended|unrated|remastered|final\s*cut|ultimate\s*edition|special\s*edition|theatrical\s*cut)\b',
                    r'\b(?:criterion\s*collection)\b',
                ]

                # Website prefix removal
                show_name = re.sub(r'^www\.[^\s]+\.\w+\s*-\s*', '', show_name, flags=re.IGNORECASE)
                print(f"After website removal: {show_name}")

                for p in quality_source_patterns:
                    show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                for p in edition_patterns:
                    show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)

                show_name = re.sub(r'\b(?:NF|WEBRip|WEB-DL|DDP\d+\.?\d*|x264|x265|H\.?264|1080p|720p|480p|4k|uhd)\b', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'\b\d+\.\d+\b', ' ', show_name)
                show_name = re.sub(r'[._]+', ' ', show_name)
                show_name = re.sub(r'\([^)]*\)', '', show_name)
                show_name = re.sub(r'\[.*?\]', '', show_name)
                show_name = re.sub(r'\bSeason\s*\d+\b', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'\bS(\d+)\b(?!\s*E\d+)', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'\b(?:japanese|english|french|german|spanish|italian|russian|korean|hindi)\b', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'-\b[A-Za-z0-9]{2,10}\b\s*$', ' ', show_name)
                show_name = re.sub(r'\s+', ' ', show_name).strip()

                print(f"Final show_name: '{show_name}'")
                print(f"Will set name to: '{show_name}'")

    # Continue with full cleaning
    cleaned_name, detected_year = clean_movie_name(input_path, patterns)
    print(f"Cleaned: {cleaned_name}")
    print(f"Year: {detected_year}")
    return cleaned_name, detected_year

if __name__ == "__main__":
    # Debug specific case if requested
    if len(sys.argv) > 1 and sys.argv[1] == "--debug":
        if len(sys.argv) > 2:
            debug_cleaning(sys.argv[2])
        else:
            print("Usage: python name_cleaning_tester.py --debug 'path/to/file'")
    else:
        sys.exit(run_tests())

