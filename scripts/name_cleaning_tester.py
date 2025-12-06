import sys

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
#
# ============================================================================
# TEST DATA POLICY - PLEASE READ BEFORE ADDING OR MODIFYING TEST CASES
# ============================================================================
# All test data in this file uses FICTIONAL names only. This is intentional.
# When adding new test cases, always substitute real names with made-up ones:
#
#   - Use invented show/movie titles (e.g., "Gerbil Titans", "Moonwaffles")
#   - Use fictional episode names (e.g., "The Fuzzy Pretzel Incident")
#   - Use fake release group names (e.g., "AGLET", "XYZ", "CtrlHD")
#
# The *structure* and *patterns* of the path matter for testing, not the names.
# A path like "Show.Name.S01E02.720p.BluRay-GROUP" tests the same parsing logic
# whether "Show Name" is real or fictional.
#
# If you find a real-world path that needs testing, recreate the same folder
# structure and naming patterns using invented names. Document what pattern
# the test validates in the comment above each case.
# ============================================================================
TEST_CASES: list[dict[str, str | None]] = [
    {
        # Tests: HDrip removal, basic title case correction
        "input": r"D:\movies\My fluffy llama.HDrip.avi",
        "expected_name": "My Fluffy Llama",
        "expected_year": None,
    },
    {
        # Tests: year in parens, quality tags (720p BRrip), uploader tags (sujaidr, pimprg)
        "input": r"D:\movies\Gertrude and Claude (1983) 720p BRrip_sujaidr (pimprg).mkv",
        "expected_name": "Gertrude and Claude",
        "expected_year": 1983,
    },
    {
        # Tests: dot-separated format, year extraction, codec (x264), uploader (YIFY)
        "input": r"D:\movies\Moonwaffles.2003.1080p.BluRay.x264.YIFY.mp4",
        "expected_name": "Moonwaffles",
        "expected_year": 2003,
    },
    {
        # Tests: short 3-letter acronym title preservation
        "input": r"D:\movies\QXJ.1994.1080p.BluRay.x264.YIFY.mp4",
        "expected_name": "QXJ",
        "expected_year": 1994,
    },
    {
        # Tests: anime-style bracketed group tag [Triad], underscore separators, episode number
        "input": r"D:\movies\Bonkus\Season 1\[Triad]_Bonkus_-_12.mkv",
        "expected_name": "Bonkus S01E12",
        "expected_year": None,
    },
    {
        # Tests: French apostrophe handling, title case with L' prefix
        "input": r"D:\movies\lomelette-1972\L'omelette (1972).mp4",
        "expected_name": "L'Omelette",
        "expected_year": 1972,
    },
    {
        # Tests: dual-language title with dash, foreign apostrophe, codec/language tags
        "input": r"D:\movies\L'anatra Pazza - The Crazy Duck (2011) 720p h264 Ac3 Ita Eng Sub Ita Eng - MIRCrew\L'anatra Pazza - The Crazy Duck (2011) 720p h264 Ac3 Ita Eng",
        "expected_name": "L'anatra Pazza - The Crazy Duck",
        "expected_year": 2011,
    },
    {
        # Tests: show with number in name, leading zeros on episode, SxxExx format
        "input": r"D:\movies\Zeppelin 9 (1986)\024 S02E01 Wobbly Banana Crisis.mkv",
        "expected_name": "Zeppelin 9 024 S02E01 Wobbly Banana Crisis",
        "expected_year": 1986,
    },
    {
        # Tests: all-caps title, JAPANESE language tag, NF/WEBRip tags, episode title with dots
        "input": r"D:\movies\GRUMPYCATS.S01.JAPANESE.1080p.NF.WEBRip.DDP2.0.x264-AGLET[rartv]\GRUMPYCATS.S01E10.A.Sock.in.Grandmas.Drawer.1080p.NF.WEB-DL.DDP2.0.H.264-AGLET.mkv",
        "expected_name": "GRUMPYCATS S01E10 A Sock in Grandmas Drawer",
        "expected_year": None,
    },
    {
        # Tests: long title with proper name, year range in brackets [1982-7], episode prefix number
        "input": r"D:\movies\The Magical Kitchen of Gustavo Fernandez [1982-7]\02-A Whiff of Tacos.mp4",
        "expected_name": "The Magical Kitchen of Gustavo Fernandez - 02 - A Whiff of Tacos",
        "expected_year": None, #because a removed item in the prior folder name doesn't mean that we lose the actaul filename
    },
    {
        # Tests: director name in parentheses with year, DivX/DVDRip tags
        "input": r"D:\movies\Cheese and Crackers (Snappy McMuffin 1991) DivX DVDRip.avi",
        "expected_name": "Cheese and Crackers",
        "expected_year": 1991,
    },

    {
        # Tests: underscores as spaces, Title1 suffix removal, all caps to title case, "from" lowercase
        "input": r"D:\movies\THE_PENGUINS_FROM_ANTARCTICA_Title1.mp4",
        "expected_name": "The Penguins from Antarctica", #title1 suffixes are bad text.  most movies _ actually is a space.
        "expected_year": None,
    },

    {
        # Tests: number at start of title, EXTENDED tag removal, nested folder with same name
        "input": r"D:\movies\17 Chipmunks (1997) EXTENDED.720p.BRrip.sujaidr\17 Chipmunks (1997) EXTENDED.720p.BRrip.sujaidr.mkv",
        "expected_name": "17 Chipmunks",
        "expected_year": 1997,
    },
    {
        # Tests: numeric-leading title with IMDB metadata in parent folder
        "input": r"D:\movies\10 Grumpy Owls (1957) IMDB 9.0\10.Grumpy.Owls.1997.1080p.BluRay.Flac.1.0.x265.HEVC-Nb8.mkv",
        "expected_name": "10 Grumpy Owls",
        "expected_year": 1997,
    },
    {
        # Tests: website prefix removal (720pMkv.Com_), dot-separated multi-word title
        "input": r"D:\movies\720pMkv.Com_The.Snickerdoodle.Conspiracy.2015.720p.BluRay.x264.mp4",
        "expected_name": "The Snickerdoodle Conspiracy",
        "expected_year": 2015,
    },
    {
        # Tests: number at very start, dot-separated words, "about" lowercase
        "input": r"D:\movies\4.or.5.Reasons.My.Cat.Ignores.Me.2001.BRRip.720p.x264-Classics\4.or.5.Reasons.My.Cat.Ignores.Me.2001.BRRip.720p.x264-Classics.mkv",
        "expected_name": "4 or 5 Reasons My Cat Ignores Me",
        "expected_year": 2001,
    },
    {
        # Tests: show with year, season range (S01-05), deeply nested path, episode title extraction
        "input": r"D:\movies\The Noodle Dimension (1978) Season 1-5 S01-05 (1080p BluRay x265 HEVC 10bit AAC 2.0 ImE)\Season 2\The Noodle Dimension (1978) - S02E24 - The Fuzzy Pretzel Incident (1080p BluRay x265 ImE).mkv",
        "expected_name": "The Noodle Dimension S02E24 The Fuzzy Pretzel Incident",
        "expected_year": 1978,
    },
    {
        # Tests: www.site.org prefix removal with dash separator
        "input": r"D:\movies\www.UIndex.org - Silly Honk Honk S02E08 720p WEB-DL AAC2 0 H 264-NTb\Silly Honk Honk S02E08 720p WEB-DL AAC2 0 H 264-NTb.mkv",
        "expected_name": "Silly Honk Honk S02E08",
        "expected_year": None,
    },
    {
        # Tests: director collection folder, year range in brackets, Criterion tag, nested subfolders
        "input": r"D:\movies\The Pierre Bonbon Quadrology [ 1985-1999 ] BluRay.720p-1080p.x264.anoXmous\03.In.The.Mood.For.Soup.2006.Criteron.Collection.1080p.BluRay.x264.anoXmous\In.The.Mood.For.Soup.2006.Criteron.Collection.1080p.BluRay.x264.anoXmous.mp4",
        "expected_name": "In the Mood for Soup",
        "expected_year": 2006,
    },
    {
        # Tests: COMPLETE series folder tag, bracketed uploader tag [TGx]
        "input": r"D:\movies\The.Pudding.Pals.S01.COMPLETE.720p.AMZN.WEBRip.x264-GalaxyTV[TGx]\The.Pudding.Pals.S01E01.720p.AMZN.WEBRip.x264-GalaxyTV.mkv",
        "expected_name": "The Pudding Pals S01E01",
        "expected_year": None,
    },
    {
        # Tests: underscore prefix folder (_done), dot-separated title
        "input": r"D:\movies\_done\Spatularella.1995.1080p.BluRay.x264-HD4U\Spatularella.1995.1080p.BluRay.x264-HD4U.mkv",
        "expected_name": "Spatularella",
        "expected_year": 1995,
    },
    {
        # Tests: author name in parentheses (with special char é), Vol1-Episode2 format
        "input": r"D:\movies\A Sneaky Hamster (Pierre le Fromage) XviD moviesbyrizzo\A Sneaky Hamster (Pierre le Fromage) Vol1-Episode2.avi",
        "expected_name": "A Sneaky Hamster Vol1-Episode2",
        "expected_year": None,
    },
    {
        # Tests: foreign title, "Season 1" folder format, numbered episode (02.), date-like episode title
        "input": r"D:\movies\Snansen - Season 1 - 720p x265 HEVC - DAN-ITA (ENG SUBS) [BRSHNKV]\02. Spaghetti Wednesday .mp4",
        "expected_name": "Snansen S01E02 Spaghetti Wednesday",
        "expected_year": None,
    },
    {
        # Tests: Complete.Series folder, Season subfolder, bracketed uploader
        "input": r"D:\movies\Gigglebox.Complete.Series-720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded]\Season 8\Gigglebox.S08E16.The.Lumpy.Potato.720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded].mkv",
        "expected_name": "Gigglebox S08E16 The Lumpy Potato",
        "expected_year": None,
    },
    {
        # Tests: www.site.lt prefix with dash, apostrophe in title, bracketed suffix
        "input": r"D:\movies\www.MovieRulz.lt - The Pancake Flipper's Handbook (2013) 720p HDRip [.Lt].mkv",
        "expected_name": "The Pancake Flipper's Handbook",
        "expected_year": 2013,
    },
    {
        # Tests: bracketed website prefix [ www.site.com ], LIMITED tag
        "input": r"D:\movies\[ www.UsaBit.com ] - The Thousand Pickle Motel 2009 LIMITED 720p BRRip x264-PLAYNOW.mp4",
        "expected_name": "The Thousand Pickle Motel",
        "expected_year": 2009,
    },
    {
        # Tests: long title with apostrophe, nested folder matching filename
        "input": r"D:\movies\One Jumped Over The Beaver's Dam (1988)\One.Jumped.Over.The.Beaver's.Dam.720p.BrRip.x264.YIFY.mp4",
        "expected_name": "One Jumped Over The Beaver's Dam",
        "expected_year": 1988,
    },
    {
        # Tests: multi-season folder (Seasons 1-2 + Extras), episode title with number (4-17), bracketed tag
        "input": r"D:\movies\Gerbil Titans - Seasons 1-2 + Extras\Gerbil Titans - Season 1\Gerbil Titans - S01E03 - Lil 4-17 [Demon].avi",
        "expected_name": "Gerbil Titans S01E03 Lil 4-17",
        "expected_year": None,
    },
    {
        # Tests: Season folder with quality tags (1080p x265 10bit), episode title
        "input": r"D:\movies\Please Phone Doug Season 1 (1080p x265 10bit Joy)\Please Phone Doug S01E01 Zilch (1080p x265 10bit Joy).mkv",
        "expected_name": "Please Phone Doug S01E01 Zilch",
        "expected_year": None,
    },
    {
        # Tests: continuation of series test, different season, bracketed uploader tag
        "input": r"D:\movies\Gerbil Titans - Seasons 1-2 + Extras\Gerbil Titans - Season 2\Gerbil Titans - S02E06 - Super Bouncy Noodle [Geophage].avi",
        "expected_name": "Gerbil Titans S02E06 Super Bouncy Noodle",
        "expected_year": None,
    },
    {
        # Tests: Complete folder tag, "Wumbo" episode title (tests generic pilot-like titles)
        "input": r"D:\movies\Discombobulated.Complete.1080p.WEB-DL Retic1337\Season 1\Discombobulated.S01E01.Wumbo.1080p.WEB-DL.mp4",
        "expected_name": "Discombobulated S01E01 Wumbo",
        "expected_year": None,
    },
    {
        # Tests: cryptic filename (group-abbreviation format), title extraction from parent folder
        # Filename "xyz-pwh1080" looks like release group code, so use parent for title
        "input": r"D:\movies\Penguin.With.Hat.1987.1080p.BluRay.x264-XYZ\xyz-pwh1080.mkv",
        "expected_name": "Penguin with Hat",
        "expected_year": 1987,
    },
    {
        # Tests: genre descriptor removal ("Film Noir"), trailing dash cleanup, bracket codec removal
        # Filename and folder are identical (common pattern)
        "input": r"D:\movies\Rainy Sidewalk - Film Noir 1953 Eng Subs 1080p [H264-mp4]\Rainy Sidewalk - Film Noir 1953 Eng Subs 1080p [H264-mp4].mp4",
        "expected_name": "Rainy Sidewalk",
        "expected_year": 1953,
    },
    {
        # Tests: hyphenated title preservation (letter-word pattern), SxxExx with episode title, nested season folders
        # Must NOT remove "-Force" as a release group suffix - it's part of the title
        "input": r"D:\movies\The Z Force S01-S05 (1987-)\The Z-Force S02 (360p re-dvdrip)\The Z-Force S02E08 Waffle Emergency.mp4",
        "expected_name": "The Z-Force S02E08 Waffle Emergency",
        "expected_year": None,
    },
    {
        # Tests: TV series with year and "Complete Seasons" in grandparent folder, Season X subfolder
        # Should extract show name from filename, not the metadata-heavy grandparent folder
        "input": r"D:\movies\Turbo Ferret 1984 Complete Seasons 1 to 4 720p BluRay x264 [i_c]\Season 2\Turbo Ferret S02E11 Nightmare Fuel.mkv",
        "expected_name": "Turbo Ferret S02E11 Nightmare Fuel",
        "expected_year": 1984,
    },
    {
        # Tests: "Season X Episode Y" format in filename (not SxxExx), comma-separated season list in grandparent,
        # "Deluxe DVD Boxset + Extras in HD" metadata, episode title after dash
        # Should extract show name from filename before "Season X Episode Y" pattern
        "input": r"D:\movies\Cosmic Llama Adventures Season 1, 2, 3, 4 & 5 Deluxe DVD Boxset + Extras in HD\Season 4\Cosmic Llama Adventures Season 4 Episode 12 - The Sparkly Nebula.avi",
        "expected_name": "Cosmic Llama Adventures S04E12 The Sparkly Nebula",
        "expected_year": None,
    },
    {
        # Tests: dot-separated filename with year before SxxExx, parent folder matches filename pattern,
        # DD5.1 audio tag removal, x264-GROUP release suffix removal
        # Should extract show name from filename (cleaner) and year, not leave DD5.1 fragments
        "input": r"D:\movies\Silly.Ducks.In.Space.1987.720p.Blu-ray.DD5.1.x264-CtrlHD\Silly.Ducks.In.Space.1987.S01E02.720p.Blu-ray.DD5.1.x264-CtrlHD.mkv",
        "expected_name": "Silly Ducks In Space S01E02",
        "expected_year": 1987,
    },
    {
        # Tests: movie in numbered collection folder (like "Complete Set"), parent has sequence number prefix,
        # filename has franchise prefix. Should use parent folder title without sequence number.
        # Example: Bond/franchise collection where each movie is in numbered subfolder
        "input": r"D:\movies\Captain.Wombat.Complete.Set.1965-2019.1080p.BluRay.x264-ETRG\10.The.Sneaky.Penguin.Caper.1979\Captain.Wombat.The.Sneaky.Penguin.Caper.1979.1080p.BluRay.x264.AC3-Ozlem.mp4",
        "expected_name": "The Sneaky Penguin Caper",
        "expected_year": 1979,
    },
    {
        # Tests: documentary series with episode number prefix and historical date range in episode title
        # The date range "(1933 to 1939)" should be preserved as episode context, NOT extracted as movie year
        # Year should come from parent folder (1973), not from episode title dates
        "input": r"D:\movies\The Galaxy At Peace (1973) Mp4 1080p\01 A New Federation (1933 to 1939).mp4",
        "expected_name": "The Galaxy At Peace - 01 - A New Federation (1933 to 1939)",
        "expected_year": 1973,
    },
]


def run_tests() -> int:
    patterns = load_cleaning_patterns()
    failures: list[dict[str, object]] = []

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
    import re
    from pathlib import Path

    from scanning import clean_movie_name, load_cleaning_patterns

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

