#!/usr/bin/env python3
"""
Script to display condensed scan logs from both memory and file.
Shows the current progress and provides access to full logs.
"""
import requests
import json
import sys
from pathlib import Path

def get_scan_progress():
    """Get current scan progress"""
    try:
        response = requests.get('http://localhost:8002/api/scan-progress', timeout=5)
        return response.json()
    except Exception as e:
        print(f"Error connecting to server: {e}")
        return None

def get_full_logs(lines=100):
    """Get full scan logs from file"""
    try:
        response = requests.get(f'http://localhost:8002/api/scan-logs?lines={lines}', timeout=5)
        return response.json()
    except Exception as e:
        print(f"Error getting full logs: {e}")
        return None

def analyze_logs(logs):
    """Analyze logs for useful statistics"""
    if not logs:
        return {}

    processing_count = 0
    indexed_count = 0
    skipped_count = 0
    error_count = 0
    warning_count = 0

    for log in logs:
        message = log.get('message', '').upper()
        if 'PROCESSING:' in message:
            processing_count += 1
        elif 'INDEXED:' in message:
            indexed_count += 1
        elif 'SKIPPED' in message:
            skipped_count += 1
        elif log.get('level') == 'error':
            error_count += 1
        elif log.get('level') == 'warning':
            warning_count += 1

    return {
        'processing': processing_count,
        'indexed': indexed_count,
        'skipped': skipped_count,
        'errors': error_count,
        'warnings': warning_count
    }

def main():
    print("=== MOVIE SEARCHER SCAN LOGS ===\n")

    # Get current progress
    progress = get_scan_progress()
    if progress:
        print("CURRENT SCAN STATUS:")
        print(f"  Status: {progress['status']}")
        print(f"  Progress: {progress['current']}/{progress['total']} ({progress['progress_percent']:.1f}%)")
        print(f"  Current file: {progress['current_file']}")
        print(f"  Memory logs: {len(progress['logs'])} entries")
        print()

        # Analyze memory logs
        if progress['logs']:
            stats = analyze_logs(progress['logs'])
            print("MEMORY LOGS SUMMARY:")
            print(f"  Processing logs: {stats['processing']}")
            print(f"  Indexed files: {stats['indexed']}")
            print(f"  Skipped files: {stats['skipped']}")
            print(f"  Errors: {stats['errors']}")
            print(f"  Warnings: {stats['warnings']}")
            print()

    # Get full logs from file
    print("FULL SCAN LOGS FROM FILE:")
    full_logs = get_full_logs(lines=50)  # Last 50 lines
    if full_logs and 'logs' in full_logs:
        print(f"  Total log lines in file: {full_logs.get('total_lines', 0)}")
        print(f"  Showing last {len(full_logs['logs'])} lines:")
        print()
        for i, line in enumerate(full_logs['logs'], 1):
            # Truncate long lines for readability
            if len(line) > 120:
                line = line[:117] + "..."
            print(f"{i:2d}. {line}")
        print()

        if full_logs.get('total_lines', 0) > len(full_logs['logs']):
            print(f"... ({full_logs['total_lines'] - len(full_logs['logs'])} more lines available)")
            print("Run: python show_full_logs.py all  # to see all logs")
            print()
    else:
        print("  No file logs available yet")
        print()

    # Show how to access more logs
    print("TO VIEW MORE LOGS:")
    print("1. Full file logs: GET http://localhost:8002/api/scan-logs")
    print("2. Last 500 lines: GET http://localhost:8002/api/scan-logs?lines=500")
    print("3. Direct file: scan_log.txt in the project directory")
    print()

if __name__ == "__main__":
    main()
