import sys
sys.path.append('.')
from scanning import scan_progress

print(f'Current scan_progress logs count: {len(scan_progress["logs"])}')
print(f'Scan status: {scan_progress["status"]}')
print(f'Current: {scan_progress["current"]}/{scan_progress["total"]}')

if scan_progress['logs']:
    print('\nFirst few logs:')
    for i, log in enumerate(scan_progress['logs'][:5]):
        print(f'  {i}: {log}')

    print('\nLast few logs:')
    for i, log in enumerate(scan_progress['logs'][-5:]):
        print(f'  {len(scan_progress["logs"]) - 5 + i}: {log}')

    # Check if there are logs about movie processing
    movie_logs = [log for log in scan_progress['logs'] if 'Processing:' in log['message'] or 'Indexed:' in log['message']]
    print(f'\nMovie processing logs found: {len(movie_logs)}')
    if movie_logs:
        print('Sample movie logs:')
        for log in movie_logs[:3]:
            print(f'  {log}')
        print('...')
        for log in movie_logs[-3:]:
            print(f'  {log}')
