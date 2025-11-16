import os
import subprocess
import json
import shlex
from pathlib import Path

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}

ps_script = (
    "Get-CimInstance Win32_Process -Filter \"name = 'vlc.exe'\" "
    "| Select-Object CommandLine, ProcessId "
    "| ConvertTo-Json -Compress"
)
result = subprocess.run(
    ["powershell", "-NoProfile", "-Command", ps_script],
    capture_output=True,
    text=True,
    timeout=5
)

print(f"1. Return code: {result.returncode}")
print(f"2. STDOUT: '{result.stdout.strip()}'")
print(f"3. Has stdout: {bool(result.stdout.strip())}")

if result.returncode != 0 or not result.stdout.strip():
    print("EARLY EXIT: returncode!=0 or empty stdout")
    exit()

try:
    data = json.loads(result.stdout)
    print(f"4. Parsed JSON successfully")
except json.JSONDecodeError as e:
    print(f"JSON parse error: {e}")
    exit()

processes = data if isinstance(data, list) else [data]
print(f"5. Processes count: {len(processes)}")
print(f"6. Processes: {processes}")

command_lines = []

for proc in processes:
    cmd_line = (proc.get("CommandLine") or "").strip()
    pid = str(proc.get("ProcessId") or "").strip()
    print(f"7. cmd_line: '{cmd_line}'")
    print(f"8. pid: '{pid}'")
    
    if not cmd_line:
        print("   SKIP: empty cmd_line")
        continue
        
    try:
        args = shlex.split(cmd_line)
        print(f"9. args after shlex: {args}")
    except Exception as e:
        print(f"   shlex failed: {e}, using split()")
        args = cmd_line.split()

    if len(args) <= 1:
        print("   SKIP: args <= 1")
        continue

    print(f"10. Checking args[1:]: {args[1:]}")
    for arg in args[1:]:
        print(f"    - Checking arg: '{arg}'")
        try:
            if arg.startswith("-"):
                print(f"      SKIP: starts with -")
                continue
            
            exists = os.path.exists(arg)
            print(f"      exists: {exists}")
            
            if exists:
                suffix = Path(arg).suffix.lower()
                print(f"      suffix: '{suffix}'")
                in_extensions = suffix in VIDEO_EXTENSIONS
                print(f"      in VIDEO_EXTENSIONS: {in_extensions}")
                
                if in_extensions:
                    command_lines.append({"path": arg, "pid": pid})
                    print(f"      ADDED!")
                    break
        except Exception as e:
            print(f"      Exception: {e}")
            continue

print(f"\n11. FINAL result: {command_lines}")

