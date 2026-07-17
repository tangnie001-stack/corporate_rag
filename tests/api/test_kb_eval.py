"""KB 评估端点测试 — eval/latest。"""

from unittest.mock import AsyncMock

from tests.api.mock_data import make_eval_report


def test_latest_eval_found(mock_app_service, auth_client):
    """POST /api/kbs/eval/latest 返回评估报告。"""
    mock_app_service.db.get_latest_eval_report = AsyncMock(
        return_value=make_eval_report(0.84, passed=True, qa_count=20)
    )

    response = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-1"})

    assert response.status_code == 200
    data = response.json()["data"]["data"]
    assert data["overall_score"] == 0.84
    assert data["passed"] is True
    assert data["qa_count"] == 20


def test_latest_eval_not_found(mock_app_service, auth_client):
    """POST /api/kbs/eval/latest 无评估报告返回 data=None。"""
    mock_app_service.db.get_latest_eval_report = AsyncMock(return_value=None)

    response = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-no-eval"})

    assert response.status_code == 200
    assert response.json()["data"]["data"] is None
