"""GET /api/skills、/api/agents：统一信封 + 服务端过滤 + 失败 fail-open。"""

from unittest.mock import MagicMock


def test_skills_envelope(auth_client, mock_app_service):
    """信封为 data.skills（前端按 body.data 解析）。"""
    mock_app_service.agent_service.capability_service.list_skills = MagicMock(
        return_value=[{"name": "finance-qa", "description": "财务问答"}]
    )
    resp = auth_client.get("/api/skills")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "SUCCESS"
    assert body["data"]["skills"][0]["name"] == "finance-qa"


def test_agents_envelope(auth_client, mock_app_service):
    """信封为 data.agents。"""
    mock_app_service.agent_service.capability_service.list_agents = MagicMock(
        return_value=[
            {"name": "finance-expert", "display_name": "财务专家", "description": "d"}
        ]
    )
    resp = auth_client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json()["data"]["agents"][0]["display_name"] == "财务专家"


def test_failure_returns_empty_list_not_500(auth_client, mock_app_service):
    """读取失败 → 空列表 + 200（fail-open，不阻断选择器渲染）。"""
    mock_app_service.agent_service.capability_service.list_skills = MagicMock(
        side_effect=RuntimeError("boom")
    )
    resp = auth_client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json()["data"]["skills"] == []
