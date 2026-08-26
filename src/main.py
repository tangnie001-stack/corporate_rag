"""FastAPI 应用入口 — app 工厂、CORS、生命周期管理。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api import auth as auth_routes
from src.api import (
    chat_router,
    clarify_router,
    doc_router,
    feedback_router,
    health_router,
    kb_eval_router,
    kb_router,
    sessions_router,
)
from src.api import ragas_generate as ragas_generate_routes
from src.config.response_codes import Code
from src.core.logging import setup_logging
from src.middleware.auth import auth_middleware
from src.middleware.response_processor import response_processor_middleware
from src.middleware.trace_id import trace_id_middleware
from src.utils.errors import AppError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期处理器 — 启动/关闭。

    数据库迁移通过 docker compose exec app alembic upgrade head 手动执行。

    启动阶段预热 ChromaDB：将首次打开持久化数据的初始化风险从
    首个用户请求移到启动阶段，避免请求侧出现
    'RustBindingsAPI' object has no attribute 'bindings' 冷启动异常。
    """
    logger.info("财务问答 API 正在启动")
    _warmup_chromadb()
    yield
    logger.info("财务问答 API 正在关闭")


def _warmup_chromadb() -> None:
    """预热 ChromaDB PersistentClient，确保冷启动风险在启动阶段暴露。

    ChromaDB 首次打开已有持久化数据时，Rust bindings 初始化存在时序竞态，
    若由首个用户请求触发会在 retrieve 阶段报 AttributeError。
    预热失败不阻塞启动：持久化数据可能尚未创建（全新部署），
    首次写入时同样能正常初始化。
    """
    from src.infra.db.vector_store import VectorStore

    try:
        store = VectorStore()
        # list_collections 会触发 PersistentClient 惰性创建 + 持久化校验
        collections = store.list_collections()
        logger.info("ChromaDB warmed up: collections={}", len(collections))
    except Exception as e:  # noqa: BLE001
        logger.warning("ChromaDB warmup failed (will retry lazily): {}", e)


app = FastAPI(
    title="财务问答 API",
    description="金融文档问答助手的 REST API — 知识库管理、文档上传、流式 RAG 问答",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
)


# 配置 Loguru — 收拢到统一模块
setup_logging(configure_trace_id=True)


# 异常处理器 — 所有 Router 层异常在此集中处理，补充 traceback 日志


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    from starlette.responses import JSONResponse

    # BusinessError 等已知业务异常用 warning 级别
    # SystemError 等基础设施异常用 exception 级别（含完整 traceback）
    if exc.status >= 500:
        logger.exception("基础设施异常: {} {}", exc.code, exc.message)
    else:
        logger.warning("业务异常: {} {}", exc.code, exc.message)

    # TODO: ARMS Prometheus 接入后在此处打 exception_total.inc()
    return JSONResponse(
        {"code": exc.code, "message": exc.message, "data": None},
        status_code=exc.status,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from starlette.responses import JSONResponse

    logger.exception("HTTP 异常: {} {}", exc.status_code, exc.detail)
    code = Code.NOT_FOUND if exc.status_code == 404 else Code.UNKNOWN_ERROR
    msg = exc.detail or (
        Code.NOT_FOUND_MSG if exc.status_code == 404 else Code.UNKNOWN_ERROR_MSG
    )
    return JSONResponse(
        {"code": code, "message": msg, "data": None}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    from starlette.responses import JSONResponse

    logger.exception("参数校验异常: {}", exc.errors())
    return JSONResponse(
        {
            "code": Code.VALIDATION_ERROR,
            "message": Code.VALIDATION_ERROR_MSG,
            "data": None,
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unknown_exception_handler(request: Request, exc: Exception):
    from starlette.responses import JSONResponse

    logger.exception("未处理的系统异常: {} {}", request.method, request.url)
    # 打印异常链根因
    c = exc
    depth = 0
    while depth < 5:
        nxt = c.__cause__ or c.__context__
        if nxt is None:
            break
        c = nxt
        logger.error("  ├─ 嵌套第{}层: type={} msg={}", depth + 1, type(c).__name__, c)
        depth += 1
    # TODO: ARMS Prometheus 接入后在此处打 exception_total.inc()
    return JSONResponse(
        {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
        status_code=500,
    )


# 中间件注册顺序（请求从外到内）：
# CORS → ResponseProcessor → auth → TraceID → router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(response_processor_middleware)
app.middleware("http")(auth_middleware)
app.middleware("http")(
    trace_id_middleware
)  # 最后注册 = 最外层，确保所有路径都写 X-Trace-ID

# 挂载路由模块
app.include_router(auth_routes.router, prefix="/api", tags=["auth"])
app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(kb_router, prefix="/api", tags=["knowledge-bases"])
app.include_router(doc_router, prefix="/api", tags=["documents"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(clarify_router, prefix="/api", tags=["chat"])
app.include_router(feedback_router, prefix="/api", tags=["feedback"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(kb_eval_router, prefix="/api", tags=["evaluation"])
app.include_router(ragas_generate_routes.router, prefix="/api", tags=["ragas"])
