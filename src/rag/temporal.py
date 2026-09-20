"""时间解析模块 — 候选年份派生、时间词粗筛、缺失判定。

供 retrieve_kb 工具内部调用；时间解析结果写入 RequestContext 供验证循环读取。
"""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config.const import TEMPORAL_RECENT_N_YEARS

# 时间词粗筛（包含判断，不要求精确匹配新变体）
_TEMPORAL_WORD_PATTERN = re.compile(r"近|这|上|今|去|几|最近|前几年")


def has_temporal_words(query: str) -> bool:
    """判断查询是否含相对时间词（正则粗筛，命中才调 LLM 解析）。

    Args:
        query: 用户查询文本

    Returns:
        True 表示含相对时间词，需要进入 LLM 时间解析
    """
    return _TEMPORAL_WORD_PATTERN.search(query) is not None


def compute_missing(years: list[int], covered: list[int]) -> list[int]:
    """计算要求年份中未被知识库覆盖的年份。

    Args:
        years: 解析出的要求覆盖年份列表
        covered: 知识库实际覆盖年份列表

    Returns:
        缺失年份列表（升序）
    """
    cover_set = set(covered)
    missing = [y for y in years if y not in cover_set]
    return sorted(missing)


async def derive_candidate_years(kb_ids: list[str]) -> list[int]:
    """从绑定 KB 文档元数据聚合候选年份，∪ 最近 3 个完整年度。

    读取 KB 文档 meta_info 的 year / report_period 实体，聚合去重排序；
    最后并入最近 3 个完整年度（排除进行中的当年，N 取 const.TEMPORAL_RECENT_N_YEARS）。
    KB 为空或元数据缺失时仍返回最近 N 年（供"这几年"触发联网询问，grilling 决策）。

    Args:
        kb_ids: 知识库 ID 列表

    Returns:
        候选年份列表（升序，可能仅含最近 N 年）
    """
    from src.infra.db.engine import session_factory
    from src.infra.db.repos.document_repo import DocumentRepo

    years: set[int] = set()
    repo = DocumentRepo(session_factory)
    for kb_id in kb_ids:
        docs = await repo.get_documents(kb_id)
        for doc in docs:
            if not doc.meta_info:
                continue
            try:
                meta = json.loads(doc.meta_info)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(meta, dict):
                continue
            entities = meta.get("entities") or {}
            year = entities.get("year")
            if year:
                try:
                    years.add(int(year))
                except (TypeError, ValueError):
                    pass
            period = entities.get("report_period") or ""
            if isinstance(period, str):
                m = re.search(r"(20\d{2})", period)
                if m:
                    years.add(int(m.group(1)))
    # 并入最近 3 个完整年度（排除进行中的当年）：KB 只覆盖 2024 时"这几年"
    # 也能解析出 [2023, 2025] 等缺失年份触发联网询问，否则核心 bug 修不掉
    this_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    for i in range(1, TEMPORAL_RECENT_N_YEARS + 1):
        years.add(this_year - i)
    return sorted(years)


_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _fallback_recent_years(candidates: list[int]) -> list[int]:
    """LLM 失败时回退：最近 N 个完整年度 ∩ 候选（升序，与正常解析路径一致）。"""
    this_year = datetime.now(_BEIJING_TZ).year
    recent = [this_year - i for i in range(1, TEMPORAL_RECENT_N_YEARS + 1)]
    cand = set(candidates)
    return sorted(y for y in recent if y in cand)


async def parse_temporal(query: str, candidates: list[int], llm) -> dict:
    """LLM 解析相对时间词为候选集合内的年份（含代码校验）。

    Args:
        query: 含相对时间词的查询
        candidates: 候选年份集合（KB 元数据 ∪ 最近 N 年派生，可为空）
        llm: ChatOpenAI 实例（get_classify_llm()，flash）

    Returns:
        {"years": [...], "has_temporal": bool}；LLM 失败/输出越界时回退最近 3 年 ∩ 候选
    """
    if not candidates:
        return {"years": [], "has_temporal": False}
    today = datetime.now(_BEIJING_TZ).date()
    prompt = (
        "你是时间解析器。把用户查询中的相对时间词解析为具体年份，"
        f"只能从候选集合中选择，不得输出候选之外的年份。\n"
        f"今天是 {today.year}年{today.month}月{today.day}日。\n"
        f"候选年份: {candidates}\n查询: {query}\n"
        '输出 JSON: {"years": [年份列表]}（完整年度，排除进行中的当年）'
    )
    from langchain_core.messages import HumanMessage

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)], temperature=0)
        if resp is not None:
            raw = resp.content or ""
        else:
            raw = ""
        raw = raw.strip()
        data = json.loads(raw)
        years_raw = data.get("years") or []
        years = [int(y) for y in years_raw if isinstance(y, (int, str))]
    except Exception:  # noqa: BLE001  # LLM 调用异常：已命中时间词但解析失败，回退最近 N 年
        return {"years": _fallback_recent_years(candidates), "has_temporal": True}
    if not years:
        # LLM 正常返回空列表（判定无时间约束）：走无时间约束默认路径，不强加最近 N 年
        return {"years": [], "has_temporal": False}
    cand = set(candidates)
    valid = [y for y in years if y in cand]
    if not valid:
        # LLM 正常返回但全部越界候选：回退最近 N 年（保持现状）
        return {"years": _fallback_recent_years(candidates), "has_temporal": True}
    return {"years": sorted(valid), "has_temporal": True}
