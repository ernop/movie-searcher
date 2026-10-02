"""Stream progress without making completion depend on the browser connection."""

import asyncio
import json
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

# Accepted operations still running; shutdown waits for these before exiting.
_active = set()
_active_lock = threading.Lock()


def wait_for_background_operations(timeout):
    """Let accepted searches finish and save during a server restart. Returns how many were still running."""
    with _active_lock:
        running = list(_active)
    if running:
        logger.info("Waiting up to %ss for %d accepted AI search(es) to finish before exiting", timeout, len(running))
    deadline = time.monotonic() + timeout
    for thread in running:
        thread.join(max(0, deadline - time.monotonic()))
    with _active_lock:
        left = len(_active)
    if left:
        logger.warning("%d AI search(es) were still running at shutdown and will be lost", left)
    return left


async def stream_sse_in_background(operation, heartbeat_seconds=10):
    """Finish an accepted operation, including saving, if its client disconnects."""
    events = queue.Queue()
    disconnected = threading.Event()
    finished = object()

    def produce():
        try:
            for event in operation():
                if not disconnected.is_set():
                    events.put(event)
        except Exception:
            logger.exception("Background SSE operation failed")
            if not disconnected.is_set():
                detail = "Search failed before completion. Check the server log for details."
                events.put(f'data: {json.dumps({"type": "error", "detail": detail})}\n\n')
        finally:
            events.put(finished)
            with _active_lock:
                _active.discard(threading.current_thread())

    # The producer owns the generator and its database sessions. Closing the
    # response must not close it at a progress yield before results are saved.
    # Not a daemon: the interpreter also waits for it if shutdown skips the hook.
    worker = threading.Thread(target=produce, name="ai-search")
    with _active_lock:
        _active.add(worker)
    worker.start()
    try:
        while True:
            try:
                event = await asyncio.to_thread(events.get, True, heartbeat_seconds)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if event is finished:
                break
            yield event
    finally:
        disconnected.set()
