from vlc_integration import get_vlc_command_lines
import json

print("Testing get_vlc_command_lines()...")
result = get_vlc_command_lines()
print(f"Result: {json.dumps(result, indent=2)}")

