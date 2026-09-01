"""时间解析模块 — 候选年份派生、时间词粗筛、缺失判定。

供 retrieve_kb 工具内部调用；时间解析结果写入 RequestContext 供验证循环读取。
"""

import re

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
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.config.const import TEMPORAL_RECENT_N_YEARS
    from src.infra.db.engine import session_factory
    from src.infra.db.mysql_db.document_repo import DocumentRepo

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
