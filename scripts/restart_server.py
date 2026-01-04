#!/usr/bin/env python3
"""
Server restart helper script.

This script is spawned by the restart endpoint to handle graceful server restarts.
It waits for the old server to release the port, then starts a new server.

Usage (internal, called by restart endpoint):
    python scripts/restart_server.py
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# Configuration
SERVER_PORT = 8002
MAX_WAIT_SECONDS = 15
POLL_INTERVAL = 0.3

def is_port_in_use(port: int) -> bool:
    """Check if a port is in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return False
        except OSError:
            return True

def wait_for_port_free(port: int, max_wait: float = MAX_WAIT_SECONDS) -> bool:
    """Wait for port to be free. Returns True if port is free, False if timeout."""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if not is_port_in_use(port):
            return True
        time.sleep(POLL_INTERVAL)
    return False

def main():
    # Get the project root (parent of scripts/)
    script_dir = Path(__file__).parent.absolute()
    project_root = script_dir.parent

    # Change to project root
    os.chdir(project_root)

    print(f"[Restart] Waiting for port {SERVER_PORT} to be free...")

    # Wait for the old server to release the port
    if not wait_for_port_free(SERVER_PORT):
        print(f"[Restart] ERROR: Port {SERVER_PORT} still in use after {MAX_WAIT_SECONDS}s")
        print("[Restart] The old server may not have exited properly.")
        print("[Restart] Please manually stop any existing server and run: python server.py")
        sys.exit(1)

    print(f"[Restart] Port {SERVER_PORT} is free. Starting new server...")

    # Small delay to ensure clean startup
    time.sleep(0.2)

    # Start the server
    # We exec into server.py so this process becomes the server
    # This way the server runs in this terminal/process
    server_script = project_root / "server.py"

    # Use exec on Unix, subprocess on Windows
    if sys.platform != "win32":
        os.execv(sys.executable, [sys.executable, str(server_script)])
    else:
        # On Windows, exec doesn't work the same way
        # Run the server and wait for it
        result = subprocess.run(
            [sys.executable, str(server_script)],
            cwd=str(project_root)
        )
        sys.exit(result.returncode)

if __name__ == "__main__":
    main()

