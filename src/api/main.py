"""FastAPI 应用入口 — app 工厂、CORS、生命周期管理。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from loguru import logger

from src.api.routes import (
    health_router,
    kb_router,
    doc_router,
    chat_router,
    sessions_router,
)
from src.api.routes import auth as auth_routes
from src.config.response_codes import Code
from src.infra.llm.trace_context import current_trace_id as _trace_var
from src.middleware.auth import auth_middleware
from src.middleware.response_envelope import ResponseEnvelopeMiddleware
from src.middleware.trace_id import trace_id_middleware
from src.infra.db.mysql_db import MySQLDB


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期处理器 — 启动/关闭。

    在应用启动时初始化数据库表，关闭时记录日志。
    使用 @asynccontextmanager 包装，FastAPI 会在启动和关闭时自动调用。

    Yields:
        None: 应用运行期间的上下文标记
    """
    logger.info("财务问答 API 正在启动")
    # 初始化数据库表（幂等操作，每次启动都可安全调用）
    db = MySQLDB()
    await db.init_db()
    await db.close()
    yield
    logger.info("财务问答 API 正在关闭")


app = FastAPI(
    title="财务问答 API",
    description="金融文档问答助手的 REST API — 知识库管理、文档上传、流式 RAG 问答",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
)

# 配置 loguru 自动注入 trace_id
def _trace_id_patcher(record):
    record["extra"]["trace_id"] = _trace_var.get() or ""


logger.configure(extra={"trace_id": ""}, patcher=_trace_id_patcher)

# 异常处理器 — 将 FastAPI 内置异常包装为统一格式（直接返回 JSON，不经过中间件）
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from starlette.responses import JSONResponse
    code = Code.NOT_FOUND if exc.status_code == 404 else Code.UNKNOWN_ERROR
    msg = exc.detail or (Code.NOT_FOUND_MSG if exc.status_code == 404 else Code.UNKNOWN_ERROR_MSG)
    return JSONResponse({"code": code, "message": msg, "data": None}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    from starlette.responses import JSONResponse
    return JSONResponse(
        {"code": Code.VALIDATION_ERROR, "message": Code.VALIDATION_ERROR_MSG, "data": None},
        status_code=422,
    )


# 中间件注册顺序（请求从外到内）：
# CORS → TraceID → ResponseEnvelope → auth → router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(trace_id_middleware)  # 放在 CORS 之后，ResponseEnvelope 之前

app.add_middleware(ResponseEnvelopeMiddleware)
app.middleware("http")(auth_middleware)

# 挂载路由模块
app.include_router(auth_routes.router, prefix="/api", tags=["auth"])
app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(kb_router, prefix="/api", tags=["knowledge-bases"])
app.include_router(doc_router, prefix="/api", tags=["documents"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
