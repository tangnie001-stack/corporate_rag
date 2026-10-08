"""澄清答案编排（下沉自 api 层）：单次消费、落库、文本格式化。"""

from __future__ import annotations

import asyncio

import pytest

from src.infra.llm.request_context import pending_asks
from src.services import clarify_service


class _FakeChatManager:
    """记录 add_message_async 调用的假 ChatManager（对齐真实 AppService.chat_manager）。"""

    def __init__(self) -> None:
        self.added: list[tuple[str, str, str]] = []

    async def add_message_async(
        self, session_id: str, role: str, content: str, **kwargs
    ) -> None:
        self.added.append((session_id, role, content))


class _FakeSvc:
    """记录落库调用的假 AppService（方法签名对齐真实 AppService）。"""

    def __init__(self, kb_id: str | None = "kb-1") -> None:
        self.chat_manager = _FakeChatManager()
        # 记录 (session_id, user_msg, kb_id)，与断言核对口径一致
        self.saved: list[tuple[str, str, str]] = []
        self._kb_id = kb_id

    async def get_session_by_id(self, session_id: str) -> dict | None:
        if self._kb_id is None:
            return None
        return {"kb_id": self._kb_id}

    async def save_user_async(self, session_id: str, kb_id: str, user_msg: str) -> None:
        self.saved.append((session_id, user_msg, kb_id))


@pytest.fixture(autouse=True)
def _clean_pending():
    """每个用例前后清空进程级挂起表，避免跨用例污染。"""
    pending_asks.clear()
    yield
    pending_asks.clear()


def test_format_answers_text_selected_and_custom():
    text = clarify_service.format_answers_text(
        [
            {"id": "q1", "selected": ["甲", "乙"], "custom": ""},
            {"id": "q2", "selected": [], "custom": "自定义说明"},
        ]
    )
    assert text == "甲、乙；自定义说明"


def test_format_answers_text_skips_empty_items():
    assert (
        clarify_service.format_answers_text(
            [{"id": "q1", "selected": [], "custom": ""}]
        )
        == ""
    )


@pytest.mark.asyncio
async def test_resolve_returns_false_when_no_pending():
    svc = _FakeSvc()

    ok = await clarify_service.resolve_clarify_answer(
        svc,  # type: ignore[reportArgumentType]
        session_id="S1",
        answers=[{"id": "q1", "selected": ["甲"], "custom": ""}],
    )

    assert ok is False
    assert svc.chat_manager.added == []
    assert svc.saved == []


@pytest.mark.asyncio
async def test_resolve_returns_false_when_future_already_done():
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    done.set_result([])
    pending_asks["S1"] = done
    svc = _FakeSvc()

    ok = await clarify_service.resolve_clarify_answer(
        svc,  # type: ignore[reportArgumentType]
        session_id="S1",
        answers=[{"id": "q1", "selected": ["甲"], "custom": ""}],
    )

    assert ok is False
    assert "S1" not in pending_asks  # pop 已发生（单次消费）
    assert svc.chat_manager.added == []


@pytest.mark.asyncio
async def test_resolve_delivers_answers_and_persists():
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending_asks["S1"] = fut
    svc = _FakeSvc(kb_id="kb-9")
    answers = [{"id": "q1", "selected": ["甲"], "custom": ""}]

    ok = await clarify_service.resolve_clarify_answer(
        svc,  # type: ignore[reportArgumentType]
        session_id="S1",
        answers=answers,
    )

    assert ok is True
    assert fut.result() == answers  # 原样投递给挂起的 ask_user
    assert "S1" not in pending_asks  # 单次消费
    assert svc.chat_manager.added == [("S1", "user", "甲")]
    assert svc.saved == [("S1", "甲", "kb-9")]


@pytest.mark.asyncio
async def test_resolve_skips_persist_when_text_empty():
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending_asks["S1"] = fut
    svc = _FakeSvc()

    ok = await clarify_service.resolve_clarify_answer(
        svc,  # type: ignore[reportArgumentType]
        session_id="S1",
        answers=[{"id": "q1", "selected": [], "custom": ""}],
    )

    assert ok is True
    assert svc.chat_manager.added == []  # 无文本可落时不得写空消息
    assert svc.saved == []


@pytest.mark.asyncio
async def test_resolve_tolerates_missing_session_row():
    """会话查不到（kb_id 缺失）时不得抛；Redis 已写、MySQL 跳过。"""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending_asks["S1"] = fut
    svc = _FakeSvc(kb_id=None)

    ok = await clarify_service.resolve_clarify_answer(
        svc,  # type: ignore[reportArgumentType]
        session_id="S1",
        answers=[{"id": "q1", "selected": ["甲"], "custom": ""}],
    )

    assert ok is True
    assert svc.chat_manager.added == [("S1", "user", "甲")]
    assert svc.saved == []
