"""LLM 调用内容记录 — 通过 langchain callback 在模型工厂统一收口。

所有模型实例都从 src/models.py 的 get_llm()/get_classify_llm() 创建，
在这两处给 ChatOpenAI 挂上本 handler 后，无论调用来自 graph 节点、
流式生成、ragas 指标打分（LangchainLLMWrapper 内部走 generate_prompt）
还是分类/路由，都会触发 on_llm_start / on_llm_end 记录输入输出。

受 settings.LLM_LOG_CONTENT 开关控制（默认关闭），仅调试时开启，
避免生产环境日志被 LLM 内容刷屏。
"""

from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger

# 单次记录的内容截断长度，防止超大 prompt（如表格上下文）刷爆日志
_CONTENT_LIMIT = 8000


class LlmContentLoggingHandler(BaseCallbackHandler):
    """记录每次 LLM 调用的输入与输出文本。"""

    def __init__(self) -> None:
        """初始化，缓存当前调用的模型名（on_llm_end 拿不到 serialized）。"""
        self._model: str = "unknown"

    def _model_name(self, serialized: dict, kwargs: dict) -> str:
        """从序列化信息或调用参数中提取模型名。"""
        name = ""
        if isinstance(serialized, dict):
            name = serialized.get("name", "") or ""
        invocation = kwargs.get("invocation_params") or {}
        return str(invocation.get("model") or name or "unknown")

    def on_llm_start(self, serialized: dict, prompts: list, **kwargs) -> None:
        """LLM 调用开始时记录输入，并缓存模型名供 on_llm_end 使用。"""
        self._model = self._model_name(serialized, kwargs)
        text = _format_prompts(prompts)
        logger.info(
            "[LLM:{}] START trace={} 输入: {}",
            self._model,
            _trace(),
            text[:_CONTENT_LIMIT],
        )

    def on_llm_end(self, response, **kwargs) -> None:
        """LLM 调用结束时记录输出。"""
        output = _format_response(response)
        logger.info(
            "[LLM:{}] END trace={} 输出: {}",
            self._model,
            _trace(),
            output[:_CONTENT_LIMIT],
        )


def _trace() -> str:
    """当前 trace_id（由 loguru patcher 使用，此处显式带出便于排查）。"""
    from src.infra.llm.trace_context import current_trace_id

    return current_trace_id.get() or "-"


def _format_prompts(prompts: list) -> str:
    """把 prompts 列表转成文本。

    prompts 元素可能是字符串（非 chat 模型）或 BaseMessage 列表（chat 模型）。
    """
    parts: list[str] = []
    for p in prompts:
        if isinstance(p, str):
            parts.append(p)
        else:
            parts.append("\n".join(str(m) for m in p))
    return "\n---\n".join(parts)


def _format_response(response) -> str:
    """从 LLMResult 提取生成文本。"""
    try:
        generations = getattr(response, "generations", []) or []
        texts = [g.text for g in generations[0]] if generations else []
        return "\n".join(texts)
    except Exception:  # noqa: BLE001
        return str(response)[:_CONTENT_LIMIT]
