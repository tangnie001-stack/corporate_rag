"""忠实度校验 — judge LLM 核对答案事实点是否被引用上下文支撑（原 verify_node.py 迁移）。"""

from src.config import settings
from src.core import logging as core_logging
from src.core.log_events import Event


async def faithfulness_check(answer: str, contexts: list) -> list[str]:
    """用 RAGAS_LLM_MODEL judge 核对答案事实点是否被引用上下文支撑。

    Args:
        answer: 答案文本
        contexts: 引用上下文（RequestContext.tool_contexts 的 content 列表）

    Returns:
        无支撑句子清单（judge 只标记，不删内容）
    """
    if not contexts:
        return []
    from src.models import get_llm

    llm = get_llm(
        model=settings.RAGAS_LLM_MODEL, temperature=0
    )  # 评估专用模型（RAGAS_LLM_MODEL，非 get_classify_llm）
    evidence_parts = []
    for c in contexts:
        if hasattr(c, "content"):
            evidence_parts.append(c.content)
        else:
            evidence_parts.append(str(c))
    evidence = "\n".join(evidence_parts)[:8000]
    prompt = (
        "检查回答中的每个事实点是否被引用证据支撑。\n"
        f"引用证据:\n{evidence}\n回答:\n{answer}\n"
        '输出无支撑句子清单（JSON {"unsupported": ["句子1", ...]}，全部有支撑则空数组）'
    )
    from langchain_core.messages import HumanMessage

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)], temperature=0)
        if resp is not None:
            content = resp.content
        else:
            content = None
        if isinstance(content, str):
            raw = content.strip()
        else:
            raw = ""
        import json

        data = json.loads(raw)
        unsupported = data.get("unsupported", [])
        if not isinstance(unsupported, list):
            return []
        return [s for s in unsupported if s.strip()]
    except Exception as exc:  # noqa: BLE001  # judge 失败不阻断流程，降级返回无标记，留日志
        core_logging.log_event(Event.JUDGE_FAILED, answer_len=len(answer), err=str(exc))
        return []
