import subprocess
import json

ps_script = (
    "Get-CimInstance Win32_Process -Filter \"name = 'vlc.exe'\" "
    "| Select-Object CommandLine, ProcessId "
    "| ConvertTo-Json -Compress"
)

print("PowerShell script:")
print(ps_script)
print("\n" + "="*50 + "\n")

result = subprocess.run(
    ["powershell", "-NoProfile", "-Command", ps_script],
    capture_output=True,
    text=True,
    timeout=5
)

print(f"Return code: {result.returncode}")
print(f"STDOUT: {result.stdout}")
print(f"STDERR: {result.stderr}")

if result.returncode == 0 and result.stdout.strip():
    try:
        data = json.loads(result.stdout)
        print(f"\nParsed JSON: {json.dumps(data, indent=2)}")
    except json.JSONDecodeError as e:
        print(f"\nJSON parse error: {e}")

