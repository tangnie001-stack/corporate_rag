"""能力清单只读接口 —— GET /api/skills、GET /api/agents（design D19）。"""

from fastapi import APIRouter, Depends

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.services.app_service import AppService

router = APIRouter()


@router.get("/skills", response_model=ResponseModel)
async def list_skills(svc: AppService = Depends(get_app_service)):
    """返回可被用户调用的技能清单。

    Returns:
        ResponseModel: data 为 {"skills": [{"name", "description"}]}；读取失败返回空列表（fail-open）
    """
    from src.core import logging as core_logging
    from src.core.log_events import Event

    try:
        skills = svc.agent_service.capability_service.list_skills()
    except Exception:  # noqa: BLE001 —— 配置类错误 fail-open，不阻断前端渲染
        core_logging.log_event(
            Event.CAPABILITY_DEGRADED, resource="skills", reason="read_failed"
        )
        skills = []
    return ResponseModel(data={"skills": skills})


@router.get("/agents", response_model=ResponseModel)
async def list_agents(svc: AppService = Depends(get_app_service)):
    """返回全部可加载的智能体预设清单（含 display_name）。

    Returns:
        ResponseModel: data 为 {"agents": [{"name", "display_name", "description"}]}
    """
    from src.core import logging as core_logging
    from src.core.log_events import Event

    try:
        agents = svc.agent_service.capability_service.list_agents()
    except Exception:  # noqa: BLE001
        core_logging.log_event(
            Event.CAPABILITY_DEGRADED, resource="agents", reason="read_failed"
        )
        agents = []
    return ResponseModel(data={"agents": agents})
