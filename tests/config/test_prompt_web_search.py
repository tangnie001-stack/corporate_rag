"""FINANCIAL_SYSTEM_PROMPT 重写契约：包含判定/兜底关键指令。"""

from src.config.prompts import FINANCIAL_SYSTEM_PROMPT


def test_prompt_contains_web_fallback_rules():
    """prompt 必须包含判定流程与 web 兜底的关键约束。"""
    required = (
        "先调用 retrieve_kb",  # 一律先检索
        "不要预先猜测问题是否在知识库范围内",  # 不预判
        "至少一个核心实体",  # 判定标准：含核心实体才算相关
        "该问题不在当前知识库范围内",  # web 兜底文案
        "search_web",  # 联网工具
        "换一种问法",  # 换词再检
        "top_k=10",  # 第二枪加大候选
        "知识库能回答的问题不要调用 search_web",  # 防滥用 guard
        "未在文档中找到相关数据",  # 纯拒答最后手段
    )
    for phrase in required:
        assert phrase in FINANCIAL_SYSTEM_PROMPT
