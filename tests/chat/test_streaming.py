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
