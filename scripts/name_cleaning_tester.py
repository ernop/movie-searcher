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
        "expected_name": "L'avare",
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


if __name__ == "__main__":
    sys.exit(run_tests())

