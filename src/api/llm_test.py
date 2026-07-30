"""LLM 连通性测试 API — 直接调用 LLM 验证模型可用性和响应速度。"""

from fastapi import APIRouter, Depends
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import BaseModel

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.services.app_service import AppService

router = APIRouter()


class LlmTestRequest(BaseModel):
    """LLM 测试请求。"""

    model: str = ""  # 空值表示使用默认模型
    prompt: str = "你好，请回复OK"  # 测试提示词
    temperature: float = 0  # 生成温度


@router.post("/llm/test", response_model=ResponseModel)
async def llm_test(body: LlmTestRequest, svc: AppService = Depends(get_app_service)):
    """测试 LLM 连通性 — 发送一条简单请求验证模型可用性和响应耗时。

    Args:
        model: 模型名（默认 settings.LLM_MODEL）
        prompt: 测试提示词
        temperature: 生成温度

    Returns:
        ResponseModel: data 包含 model, response, latency_seconds
    """
    model_name = body.model or svc.settings.LLM_MODEL
    logger.info("LLM test requested: model={} prompt={}", model_name, body.prompt[:50])

    import time

    start = time.time()
    try:
        llm = ChatOpenAI(
            model=model_name,
            temperature=body.temperature,
            api_key=svc.settings.LLM_API_KEY,
            base_url=svc.settings.LLM_BASE_URL,
        )
        result = llm.invoke(body.prompt)
        elapsed = round(time.time() - start, 2)
        logger.info("LLM test OK: model={} latency={}s", model_name, elapsed)
        return ResponseModel(
            data={
                "model": model_name,
                "response": result.content,
                "latency_seconds": elapsed,
            }
        )
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(
            "LLM test failed: model={} latency={}s error={}", model_name, elapsed, e
        )
        return ResponseModel(
            code="ERROR",
            message=f"LLM 调用失败: {e}",
            data={
                "model": model_name,
                "latency_seconds": elapsed,
                "error": str(e),
            },
        )
