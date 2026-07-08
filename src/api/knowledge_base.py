"""知识库 CRUD 端点。"""

from fastapi import APIRouter, Request

from src.api.model.request import CreateKBRequest, KBDeleteRequest
from src.api.model.response import CreateKBResponse, KBItem, KBDeleteResponse
from src.app_service import AppService
from src.config.response_codes import Code
from src.infra.errors import BusinessError

router = APIRouter()

# 单例服务实例（延迟初始化）
_service: AppService | None = None


def _get_service() -> AppService:
    """获取 AppService 单例实例。

    延迟初始化：首次调用时创建实例，后续复用。
    避免模块导入阶段产生网络或数据库连接。

    Returns:
        AppService 全局唯一实例
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service


@router.post("/kbs/list")
async def list_knowledge_bases(request: Request):
    """列出当前用户的所有知识库。

    Args:
        request: 请求对象，从 state 中获取 user_id

    Returns:
        list[KBItem]: 知识库列表，每项含 id、name、doc_count
    """
    svc = _get_service()
    user_id = getattr(request.state, "user_id", "")
    kbs = await svc.list_knowledge_bases(user_id)
    return [KBItem(id=kb["id"], name=kb["name"], doc_count=kb["doc_count"]) for kb in kbs]


@router.post("/kbs", status_code=201)
async def create_knowledge_base(
    request: Request, body: CreateKBRequest
) -> CreateKBResponse:
    """创建知识库（名称重复时返回已有库）。

    Args:
        body: 创建请求体，包含 name 和 description

    Returns:
        CreateKBResponse: 含新知识库 id 和是否新建标记
    """
    svc = _get_service()
    user_id = getattr(request.state, "user_id", "")
    kb_id, is_new = await svc.create_knowledge_base(
        body.name, body.description, user_id=user_id
    )
    return CreateKBResponse(id=kb_id, created=is_new)


@router.post("/kbs/delete")
async def delete_knowledge_base(body: KBDeleteRequest) -> KBDeleteResponse:
    """删除知识库及其向量数据。

    Args:
        body: 删除请求体，包含 kb_id

    Returns:
        KBDeleteResponse: 删除结果和提示消息

    Raises:
        BusinessError: 知识库不存在时返回 404
    """
    svc = _get_service()
    success, message = await svc.delete_knowledge_base(body.kb_id)
    if not success:
        raise BusinessError(Code.KB_NOT_FOUND, Code.KB_NOT_FOUND_MSG, 404)
    return KBDeleteResponse(success=True, message=message)
