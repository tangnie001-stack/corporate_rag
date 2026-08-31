import asyncio

import pytest

from src.chat.streaming import StreamingRunManager


@pytest.fixture
def mgr():
    return StreamingRunManager()


@pytest.mark.asyncio
async def test_register_unregister_is_running(mgr):
    async def noop():
        await asyncio.sleep(0.01)

    task = asyncio.create_task(noop())
    signal = asyncio.Event()
    mgr.register("s1", task, signal)
    assert mgr.is_running("s1") is True
    await task
    mgr.unregister("s1")
    assert mgr.is_running("s1") is False
    assert mgr.get_abort_signal("s1") is None


def test_set_abort_sets_event(mgr):
    signal = asyncio.Event()
    mgr._abort_signals["s1"] = signal
    mgr.set_abort("s1")
    assert signal.is_set()


@pytest.mark.asyncio
async def test_buffer_seq_and_lifecycle(mgr):
    mgr.clear_buffer("s1")
    seq1 = mgr.add_event("s1", "status", {"stage": "retrieving"})
    seq2 = mgr.add_event("s1", "token", "你好")
    assert seq1 == 1 and seq2 == 2
    assert mgr.buffer_exists("s1") is True
    events = mgr.get_events_since("s1", 1)
    assert events == [(2, "token", "你好")]
    assert mgr.has_terminal("s1") is False

    mgr.add_event("s1", "done", {"trace_id": "t"})
    assert mgr.has_terminal("s1") is True
    mgr.clear_buffer("s1")
    assert mgr.buffer_exists("s1") is False
    assert mgr.get_buffer_max_seq("s1") == 0


@pytest.mark.asyncio
async def test_buffer_cap_drops_oldest(mgr):
    mgr.clear_buffer("s1")
    for i in range(StreamingRunManager.MAX_ITEMS + 10):
        mgr.add_event("s1", "token", f"t{i}")
    assert len(mgr._stream_buffers["s1"]) <= StreamingRunManager.MAX_ITEMS
