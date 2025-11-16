from vlc_integration import get_vlc_command_lines, get_currently_playing_movies
import json

print("="*60)
print("Step 1: Test get_vlc_command_lines()")
print("="*60)
vlc_procs = get_vlc_command_lines()
print(f"VLC processes found: {len(vlc_procs)}")
for proc in vlc_procs:
    print(f"  - Path: {proc['path']}")
    print(f"  - PID: {proc['pid']}")

print("\n" + "="*60)
print("Step 2: Test get_currently_playing_movies()")
print("="*60)
playing = get_currently_playing_movies()
print(f"Movies found in database: {len(playing)}")
for movie in playing:
    print(f"  - ID: {movie['id']}")
    print(f"  - Name: {movie['name']}")
    print(f"  - Path: {movie['path']}")
    print(f"  - PID: {movie['pid']}")

print("\n" + "="*60)
print("Final JSON output:")
print("="*60)
print(json.dumps({"playing": playing}, indent=2))

