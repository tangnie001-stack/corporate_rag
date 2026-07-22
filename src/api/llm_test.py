"""LLM 连通性测试 API — 直接调用 LLM 验证模型可用性和响应速度。"""

from fastapi import APIRouter
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import BaseModel

from src.api.model.response import BaseResponse
from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL

router = APIRouter()


class LlmTestRequest(BaseModel):
    """LLM 测试请求。"""

    model: str = settings.LLM_MODEL  # 测试用的模型名，默认当前 LLM_MODEL
    prompt: str = "你好，请回复OK"  # 测试提示词
    temperature: float = 0  # 生成温度


@router.post("/llm/test")
async def llm_test(body: LlmTestRequest) -> BaseResponse:
    """测试 LLM 连通性 — 发送一条简单请求验证模型可用性和响应耗时。

    Args:
        model: 模型名（默认 settings.LLM_MODEL）
        prompt: 测试提示词
        temperature: 生成温度

    Returns:
        BaseResponse: data 包含 model, response, latency_seconds
    """
    logger.info("LLM test requested: model={} prompt={}", body.model, body.prompt[:50])

    import time

    start = time.time()
    try:
        llm = ChatOpenAI(
            model=body.model,
            temperature=body.temperature,
            api_key=DASHSCOPE_API_KEY,
            base_url=DASHSCOPE_BASE_URL,
        )
        result = llm.invoke(body.prompt)
        elapsed = round(time.time() - start, 2)
        logger.info("LLM test OK: model={} latency={}s", body.model, elapsed)
        return BaseResponse(
            data={
                "model": body.model,
                "response": result.content,
                "latency_seconds": elapsed,
            }
        )
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(
            "LLM test failed: model={} latency={}s error={}", body.model, elapsed, e
        )
        return BaseResponse(
            code=1,
            message=f"LLM 调用失败: {e}",
            data={
                "model": body.model,
                "latency_seconds": elapsed,
                "error": str(e),
            },
        )
