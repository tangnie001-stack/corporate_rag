"""答案反馈端点测试 — /api/feedback。

直接调用端点/存储辅助函数并 mock 存储（AsyncMock），不发真实网络/DB。
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.feedback import FeedbackBody, _save_feedback, submit_feedback


@pytest.mark.asyncio
async def test_save_feedback_positive():
    """positive 反馈：写入参数含 rating/comment/session_id/message_index。"""
    repo = AsyncMock()

    await _save_feedback(
        repo, session_id="s1", message_index=2, rating="positive", comment="准"
    )

    repo.save_feedback.assert_awaited_once_with(
        session_id="s1", message_index=2, rating="positive", comment="准"
    )


@pytest.mark.asyncio
async def test_feedback_invalid_rating_rejected():
    """rating 非法：Pydantic 校验拒绝（FastAPI 层映射为 422）。"""
    bad_rating: Any = "meh"
    with pytest.raises(ValidationError):
        FeedbackBody(session_id="s1", message_index=1, rating=bad_rating)


def test_feedback_invalid_rating_returns_422():
    """rating 非法：经路由层返回 422。"""
    from src.api.dependencies import get_app_service
    from src.main import app

    app.dependency_overrides[get_app_service] = lambda: AsyncMock()
    try:
        response = TestClient(app).post(
            "/api/feedback",
            json={"session_id": "s1", "message_index": 1, "rating": "meh"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feedback_store_failure_ignored():
    """存储抛异常：_save_feedback 不抛，调用方仍视为成功（容错）。"""
    repo = AsyncMock()
    repo.save_feedback.side_effect = RuntimeError("db down")

    await _save_feedback(
        repo, session_id="s1", message_index=1, rating="positive", comment=""
    )

    repo.save_feedback.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_feedback_endpoint_success():
    """端点成功路径：返回统一 ResponseModel（data=True），写入参数完整。"""
    repo = AsyncMock()
    svc = AsyncMock()
    svc.chat_repo = repo

    result = await submit_feedback(
        FeedbackBody(session_id="s1", message_index=2, rating="positive", comment="好"),
        svc,
    )

    assert result.data is True
    repo.save_feedback.assert_awaited_once_with(
        session_id="s1", message_index=2, rating="positive", comment="好"
    )
