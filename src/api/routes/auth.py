"""认证端点 — login/verify/logout/anonymous。"""

import uuid
from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.app_service import AppService
from src.config.response_codes import Code
from src.infra.errors import AuthError
from src.infra.auth.user_auth import UserAuth

router = APIRouter()

_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


class LoginRequest(BaseModel):
    """登录请求体。"""
    account: str
    password: str


@router.post("/auth/login")
async def login(body: LoginRequest):
    svc = _get_service()
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
    await UserAuth.store_token_async(svc.redis_client, token, user_id)
    await svc.db.update_user_token(user_id, token)
    return {"token": token, "user_id": user_id}


@router.post("/auth/verify")
async def verify_token(token: str = Cookie(None)):
    if not token:
        return {"valid": False}
    svc = _get_service()
    uid = await UserAuth.get_user_id_from_token_async(svc.redis_client, token)
    return {"user_id": uid, "valid": uid is not None}


@router.post("/auth/logout")
async def logout(token: str = Cookie(None)):
    if token:
        svc = _get_service()
        await UserAuth.delete_token_async(svc.redis_client, token)
    return JSONResponse({"message": "已退出登录"})


@router.post("/auth/anonymous")
async def get_anonymous_id(user_id: str = Cookie(None)):
    if not user_id:
        user_id = str(uuid.uuid4())
    resp = JSONResponse({"user_id": user_id})
    resp.set_cookie("user_id", user_id, max_age=31536000, path="/", samesite="lax")
    return resp
