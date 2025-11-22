import requests
import json

try:
    response = requests.get('http://localhost:8002/api/scan-progress')
    data = response.json()

    print(f"Scan Status: {data['status']}")
    print(f"Progress: {data['current']}/{data['total']} ({data['progress_percent']:.1f}%)")
    print(f"Current file: {data['current_file']}")
    print(f"Logs count: {len(data['logs'])}")

    if data['logs']:
        print("\n=== FIRST 10 LOGS ===")
        for i, log in enumerate(data['logs'][:10]):
            print(f"{i+1:2d}. [{log['timestamp']}] {log['level'].upper()}: {log['message']}")

        print("\n=== LAST 10 LOGS ===")
        for i, log in enumerate(data['logs'][-10:]):
            idx = len(data['logs']) - 10 + i + 1
            print(f"{idx:2d}. [{log['timestamp']}] {log['level'].upper()}: {log['message']}")

        # Count different types of logs
        levels = {}
        for log in data['logs']:
            level = log['level']
            levels[level] = levels.get(level, 0) + 1

        print("\n=== LOG LEVEL SUMMARY ===")
        for level, count in levels.items():
            print(f"{level.upper()}: {count}")

        # Look for processing logs
        processing_logs = [log for log in data['logs'] if '[Processing:' in log['message'] or 'Processing:' in log['message']]
        if processing_logs:
            print(f"\n=== PROCESSING LOGS SAMPLE ===")
            print("First processing log:")
            print(f"  {processing_logs[0]['message']}")
            print("Last processing log:")
            print(f"  {processing_logs[-1]['message']}")
            print(f"Total processing logs: {len(processing_logs)}")

except Exception as e:
    print(f"Error: {e}")
