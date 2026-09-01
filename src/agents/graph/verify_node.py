"""验证循环节点 — 完整性/忠实度校验 + 缺失联网询问。

挂在 agent_finalize → format 之间；仅在会话绑定 KB 时生效（纯对话跳过）。
"""

import re

_YEAR_PATTERN = re.compile(r"20\d{2}")


def extract_years(answer: str) -> set[int]:
    """正则提取答案文本中的 4 位年份。

    Args:
        answer: 答案文本（AgentState.answer）

    Returns:
        年份集合（可能为空）
    """
    return {int(m) for m in _YEAR_PATTERN.findall(answer)}


def completeness_check(required: list[int], answer: str) -> list[int]:
    """比对要求覆盖年份与答案实际覆盖年份，返回缺失。

    Args:
        required: 问题要求覆盖年份（RequestContext.temporal_years）
        answer: 答案文本

    Returns:
        缺失年份列表（升序）
    """
    covered = extract_years(answer)
    missing = [y for y in required if y not in covered]
    return sorted(missing)


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
    from src.config import settings
    from src.models import get_llm

    llm = get_llm(
        model=settings.RAGAS_LLM_MODEL, temperature=0
    )  # 评估专用模型（RAGAS_LLM_MODEL，非 get_classify_llm）
    evidence = "\n".join(
        c.content if hasattr(c, "content") else str(c) for c in contexts
    )[:8000]
    prompt = (
        "检查回答中的每个事实点是否被引用证据支撑。\n"
        f"引用证据:\n{evidence}\n回答:\n{answer}\n"
        '输出无支撑句子清单（JSON {"unsupported": ["句子1", ...]}，全部有支撑则空数组）'
    )
    from langchain_core.messages import HumanMessage

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)], temperature=0)
        raw = (getattr(resp, "content", None) or "").strip()
        import json

        data = json.loads(raw)
        return [s for s in data.get("unsupported", []) if s.strip()]
    except Exception:  # noqa: BLE001  # judge 失败不阻断流程，静默返回无标记
        return []
