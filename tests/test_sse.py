"""A slow AI response must survive loss of its progress stream."""

import asyncio
import json
import threading

from utils.sse import stream_sse_in_background


def test_disconnect_during_generation_still_saves_result():
    generating = threading.Event()
    release = threading.Event()
    saved = threading.Event()

    def operation():
        yield "preparing"
        generating.set()
        assert release.wait(2)
        yield "matching"
        saved.set()
        yield "result"

    async def run():
        stream = stream_sse_in_background(operation, heartbeat_seconds=0.01)
        try:
            assert await anext(stream) == "preparing"
            assert await asyncio.to_thread(generating.wait, 1)
            assert await anext(stream) == ": keepalive\n\n"
            await stream.aclose()
        finally:
            release.set()
        assert await asyncio.to_thread(saved.wait, 1)

    asyncio.run(run())


def test_connected_client_receives_result_in_order():
    def operation():
        yield "preparing"
        yield 'data: {"type": "result", "movie_list_id": 42}\n\n'

    async def run():
        return [event async for event in stream_sse_in_background(operation)]

    assert asyncio.run(run()) == list(operation())


def test_worker_failure_is_reported_and_stream_ends(caplog):
    def operation():
        yield "preparing"
        raise RuntimeError("database save failed")

    async def run():
        return [event async for event in stream_sse_in_background(operation)]

    events = asyncio.run(run())
    assert len(events) == 2
    assert json.loads(events[1].removeprefix("data: "))["type"] == "error"
    assert "database save failed" in caplog.text


def test_cancelled_response_does_not_cancel_save():
    release = threading.Event()
    generating = threading.Event()
    saved = threading.Event()

    def operation():
        generating.set()
        assert release.wait(2)
        yield "matching"
        saved.set()

    async def run():
        stream = stream_sse_in_background(operation, heartbeat_seconds=0.01)
        read = asyncio.create_task(anext(stream))
        try:
            assert await asyncio.to_thread(generating.wait, 1)
            read.cancel()
            try:
                await read
            except asyncio.CancelledError:
                pass
        finally:
            release.set()
        assert await asyncio.to_thread(saved.wait, 1)

    asyncio.run(run())
