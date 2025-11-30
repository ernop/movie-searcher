#!/usr/bin/env python3
"""
Stop the Movie Searcher server.
Cross-platform replacement for stop.bat.
"""
import os
import sys
import socket
import signal
import subprocess

SERVER_PORT = 8002


def find_process_on_port(port: int) -> list[int]:
    """Find process IDs using the specified port"""
    pids = []
    
    if sys.platform == "win32":
        # Windows: use netstat
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        try:
                            pid = int(parts[-1])
                            if pid not in pids:
                                pids.append(pid)
                        except ValueError:
                            pass
        except Exception as e:
            print(f"Warning: Could not run netstat: {e}")
    else:
        # Unix: use lsof
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.strip().splitlines():
                try:
                    pid = int(line.strip())
                    if pid not in pids:
                        pids.append(pid)
                except ValueError:
                    pass
        except FileNotFoundError:
            # lsof not available, try ss
            try:
                result = subprocess.run(
                    ["ss", "-tlnp", f"sport = :{port}"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                # Parse ss output for PIDs
                for line in result.stdout.splitlines():
                    if "pid=" in line:
                        import re
                        matches = re.findall(r'pid=(\d+)', line)
                        for match in matches:
                            pid = int(match)
                            if pid not in pids:
                                pids.append(pid)
            except Exception:
                pass
        except Exception as e:
            print(f"Warning: Could not find processes: {e}")
    
    return pids


def kill_process(pid: int) -> bool:
    """Kill a process by PID"""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=10
            )
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception as e:
        print(f"Warning: Could not kill process {pid}: {e}")
        return False


def check_port_free(port: int) -> bool:
    """Check if port is free (server stopped)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def main():
    print("Stopping Movie Searcher server...")
    print()
    
    # Find processes on our port
    pids = find_process_on_port(SERVER_PORT)
    
    if not pids:
        if check_port_free(SERVER_PORT):
            print(f"No server running on port {SERVER_PORT}.")
        else:
            print(f"Port {SERVER_PORT} is in use but could not identify the process.")
            print("You may need to stop it manually.")
        return 0
    
    # Kill each process
    killed = 0
    for pid in pids:
        print(f"Stopping process {pid}...")
        if kill_process(pid):
            killed += 1
            print(f"  Process {pid} stopped.")
    
    print()
    if killed > 0:
        print(f"Server stopped ({killed} process(es) terminated).")
    else:
        print("Could not stop server processes.")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

