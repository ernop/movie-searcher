
from scanning import clean_movie_name, load_cleaning_patterns
import sys

def test_case():
    input_path = r"D:\movies\Seinfeld.Complete.Series-720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded]\Season 8\Seinfeld.S08E16.The.Pothole.720p.WEBrip.AAC.EN-SUB.x264-[MULVAcoded].mkv"
    expected_name = "Seinfeld S08E16 The Pothole"
    
    patterns = load_cleaning_patterns()
    cleaned_name, year = clean_movie_name(input_path, patterns)
    
    print(f"Input: {input_path}")
    print(f"Expected: '{expected_name}'")
    print(f"Actual:   '{cleaned_name}'")
    print(f"Year:     {year}")
    
    if cleaned_name == expected_name:
        print("PASS")
    else:
        print("FAIL")

if __name__ == "__main__":
    test_case()

