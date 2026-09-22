"""Stream progress without making completion depend on the browser connection."""

import asyncio
import json
import logging
import queue
import threading

logger = logging.getLogger(__name__)


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

    # The producer owns the generator and its database sessions. Closing the
    # response must not close it at a progress yield before results are saved.
    threading.Thread(target=produce, name="ai-search", daemon=True).start()
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
