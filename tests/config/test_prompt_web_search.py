"""检索与联网规则契约：按最终组装结果断言（规则已从 base 移入 sources 段）。"""

from src.rag.prompt import build_system_prompt


def _assembled() -> str:
    """组装一次绑定知识库、含联网工具的 system prompt。"""
    messages = build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=frozenset({"retrieve_kb", "ask_user", "search_web"}),
        kb_domain="general",
    )
    return "\n".join(str(m.content) for m in messages)


def test_assembled_prompt_contains_web_fallback_rules():
    """最终 prompt 必须包含判定流程与 web 兜底的关键约束。"""
    required = (
        "先调用 retrieve_kb",  # 一律先检索
        "不要预先猜测问题是否在知识库范围内",  # 不预判
        "至少一个核心实体",  # 判定标准：含核心实体才算相关
        "该问题不在当前知识库范围内",  # web 兜底文案
        "search_web",  # 联网工具
        "换一种问法",  # 换词再检
        "top_k=10",  # 第二枪加大候选
        "不要调用 search_web",  # 防滥用 guard
        "未在文档中找到相关数据",  # 纯拒答最后手段
    )
    content = _assembled()
    for phrase in required:
        assert phrase in content, phrase


def test_base_segment_no_longer_carries_retrieval_ladder():
    """检索阶梯已归 sources 段：base 段正文不得再出现这些运行时规则。"""
    from src.config.prompts import loader

    for tid in ("base-financial", "base-general"):
        content = loader.get_content(tid)
        for phrase in ("先调用 retrieve_kb", "top_k=10", "换一种问法"):
            assert phrase not in content, f"{tid} 仍含运行时规则：{phrase}"
