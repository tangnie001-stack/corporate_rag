"""认证端点 — login/verify/logout/anonymous。"""

import uuid

from fastapi import APIRouter, Cookie, Depends, Response
from loguru import logger

from src.api.model.request import LoginRequest
from src.api.model.response import LoginResponse, VerifyResponse
from src.api.schema import ResponseModel
from src.services.app_service import AppService
from src.api.dependencies import get_app_service

router = APIRouter()


@router.post("/auth/login", response_model=ResponseModel)
async def login(
    body: LoginRequest,
    response: Response,
    svc: AppService = Depends(get_app_service),
):
    """用户登录或自动注册。

    若账号不存在则自动注册，再执行登录。

    Args:
        body: 登录请求，包含 account 和 password
        response: FastAPI Response（用于设置 Cookie）
        svc: AppService 依赖

    Returns:
        ResponseModel: data 含 token 和 user_id
    """
    from src.utils.errors import BusinessError

    # 先尝试注册，账号已存在则跳过
    try:
        await svc.auth_service.register(body.account, body.password)
    except BusinessError:
        pass

    # 登录
    result = await svc.auth_service.login(body.account, body.password)

    response.set_cookie(
        key="token",
        value=result["token"],
        httponly=True,
        max_age=604800,  # 7 天
        path="/",
    )
    logger.info("Login success: user_id={}", result["user_id"])
    return ResponseModel(
        data=LoginResponse(token=result["token"], user_id=result["user_id"])
    )


@router.post("/auth/verify", response_model=ResponseModel)
async def verify_token(
    token: str = Cookie(None),
    svc: AppService = Depends(get_app_service),
):
    """校验登录 token 是否有效。

    Args:
        token: 存储在 Cookie 中的登录 token
        svc: AppService 依赖

    Returns:
        ResponseModel: data 含 valid 和 user_id
    """
    if not token:
        return ResponseModel(data=VerifyResponse(valid=False, user_id=None))
    user_id = await svc.auth_service.verify_token(token)
    if user_id:
        return ResponseModel(data=VerifyResponse(valid=True, user_id=user_id))
    return ResponseModel(data=VerifyResponse(valid=False, user_id=None))


@router.post("/auth/logout", response_model=ResponseModel)
async def logout(
    token: str = Cookie(None),
    svc: AppService = Depends(get_app_service),
):
    """退出登录，清除 token。

    Args:
        token: 存储在 Cookie 中的登录 token
        svc: AppService 依赖

    Returns:
        ResponseModel: 退出提示
    """
    await svc.auth_service.logout(token)
    return ResponseModel(data={"message": "已退出登录"})


@router.post("/auth/anonymous", response_model=ResponseModel)
async def get_anonymous_id(
    user_id: str = Cookie(None),
    response: Response = None,
):
    """获取或生成匿名用户 ID。

    若 Cookie 中没有 user_id，则生成一个新的 UUID 并写入 Cookie。

    Args:
        user_id: 存储在 Cookie 中的匿名用户 ID
        response: FastAPI Response（用于设置 Cookie）

    Returns:
        ResponseModel: data 含 user_id
    """
    if not user_id:
        user_id = str(uuid.uuid4())
        response.set_cookie(
            key="user_id",
            value=user_id,
            httponly=True,
            max_age=31536000,  # 1 年
            path="/",
        )
    return ResponseModel(data={"user_id": user_id})
