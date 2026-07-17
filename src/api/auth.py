"""认证端点 — login/verify/logout/anonymous。"""

import uuid

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import JSONResponse
from loguru import logger

from src.api.model.request import LoginRequest
from src.api.model.response import LoginResponse, VerifyResponse
from src.services.app_service import AppService
from src.api.dependencies import get_app_service
from src.config.response_codes import Code
from src.infra.errors import AuthError
from src.infra.auth.user_auth import UserAuth
from src.infra.redis_client import get_redis_client

router = APIRouter()


@router.post("/auth/login")
async def login(body: LoginRequest, svc: AppService = Depends(get_app_service)) -> LoginResponse:
    """用户登录或自动注册。

    若账号存在则验证密码，不存在则自动创建账号并返回新 token。

    Args:
        body: 登录请求，包含 account 和 password

    Returns:
        LoginResponse: 登录后的 token 和用户 ID

    Raises:
        AuthError: 密码错误时抛出 401
    """
    pw_hash = UserAuth.hash_password(body.password)
    user = await svc.db.get_user_by_account(body.account)
    if user:
        if user["password"] != pw_hash:
            raise AuthError(Code.AUTH_WRONG_PASSWORD, Code.AUTH_WRONG_PASSWORD_MSG, 401)
        user_id = user["id"]
    else:
        user_id = str(uuid.uuid4())
        await svc.db.add_user(user_id, body.account, pw_hash)
        logger.info("New user registered: {}", body.account)
    token = UserAuth.generate_token()
    await UserAuth.store_token_async(get_redis_client(), token, user_id)
    await svc.db.update_user_token(user_id, token)
    return LoginResponse(token=token, user_id=user_id)


@router.post("/auth/verify")
async def verify_token(
    token: str = Cookie(None),
) -> VerifyResponse:
    """校验登录 token 是否有效。

    Args:
        token: 存储在 Cookie 中的登录 token

    Returns:
        VerifyResponse: valid 表示是否有效，user_id 为对应用户 ID
    """
    if not token:
        return VerifyResponse(valid=False)
    uid = await UserAuth.get_user_id_from_token_async(get_redis_client(), token)
    return VerifyResponse(valid=uid is not None, user_id=uid)


@router.post("/auth/logout")
async def logout(
    token: str = Cookie(None),
) -> JSONResponse:
    """退出登录，清除 token。

    Args:
        token: 存储在 Cookie 中的登录 token

    Returns:
        JSONResponse: 退出提示
    """
    if token:
        await UserAuth.delete_token_async(get_redis_client(), token)
    return JSONResponse({"message": "已退出登录"})


@router.post("/auth/anonymous")
async def get_anonymous_id(user_id: str = Cookie(None)) -> JSONResponse:
    """获取或生成匿名用户 ID。

    若 Cookie 中没有 user_id，则生成一个新的 UUID 并写入 Cookie。

    Args:
        user_id: 存储在 Cookie 中的匿名用户 ID

    Returns:
        JSONResponse: 包含 user_id，同时通过 Cookie 持久化
    """
    if not user_id:
        user_id = str(uuid.uuid4())
    resp = JSONResponse({"user_id": user_id})
    resp.set_cookie("user_id", user_id, max_age=31536000, path="/", samesite="lax")
    return resp
