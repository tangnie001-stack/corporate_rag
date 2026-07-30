"""知识库 CRUD 端点。"""

from fastapi import APIRouter, Depends, Request

from src.api.model.request import CreateKBRequest, KBDeleteRequest
from src.api.model.response import CreateKBResponse, KBItem, KBDeleteResponse
from src.api.schema import ResponseModel
from src.services.app_service import AppService
from src.api.dependencies import get_app_service
from src.config.response_codes import Code
from src.utils.errors import BusinessError

router = APIRouter()


@router.post("/kbs/list", response_model=ResponseModel)
async def list_knowledge_bases(
    request: Request, svc: AppService = Depends(get_app_service)
):
    """列出当前用户的所有知识库。

    Args:
        request: 请求对象，从 state 中获取 user_id

    Returns:
        ResponseModel: data 为知识库列表
    """
    user_id = getattr(request.state, "user_id", "")
    kbs = await svc.list_knowledge_bases(user_id)
    return ResponseModel(
        data=[
            KBItem(id=kb["id"], name=kb["name"], doc_count=kb["doc_count"])
            for kb in kbs
        ]
    )


@router.post("/kbs", status_code=201, response_model=ResponseModel)
async def create_knowledge_base(
    request: Request,
    body: CreateKBRequest,
    svc: AppService = Depends(get_app_service),
):
    """创建知识库（名称重复时返回已有库）。

    Args:
        body: 创建请求体，包含 name 和 description

    Returns:
        ResponseModel: data 含新知识库 id 和是否新建标记
    """
    user_id = getattr(request.state, "user_id", "")
    kb_id, is_new = await svc.create_knowledge_base(
        body.name, body.description, user_id=user_id
    )
    return ResponseModel(data=CreateKBResponse(id=kb_id, created=is_new))


@router.post("/kbs/delete", response_model=ResponseModel)
async def delete_knowledge_base(
    body: KBDeleteRequest, svc: AppService = Depends(get_app_service)
):
    """删除知识库及其向量数据。

    Args:
        body: 删除请求体，包含 kb_id

    Returns:
        ResponseModel: 删除结果

    Raises:
        BusinessError: 知识库不存在时返回 404
    """
    success, message = await svc.delete_knowledge_base(body.kb_id)
    if not success:
        raise BusinessError(Code.KB_NOT_FOUND, Code.KB_NOT_FOUND_MSG, 404)
    return ResponseModel(data=KBDeleteResponse(success=True, message=message))
