"""web-search-fallback 常量契约测试：文案与 ABSTENTION_MARKERS 互斥等关键不变量。"""


def test_web_search_constants():
    """web 兜底文案、kind 常量、状态事件常量就位，且兜底文案不在拒答标记里。"""
    from src.config.const import WEB_BODY_LIMIT, SSEInteractionTexts

    assert SSEInteractionTexts.WEB_SEARCH_PHRASE == "该问题不在当前知识库范围内"
    assert SSEInteractionTexts.CITATION_KIND_KB == "kb"
    assert SSEInteractionTexts.CITATION_KIND_WEB == "web"
    assert SSEInteractionTexts.STAGE_WEB_SEARCH == "web_search"
    assert SSEInteractionTexts.WEB_SEARCH_STATUS_START == "正在联网搜索..."
    assert SSEInteractionTexts.WEB_SEARCH_STATUS_END == "联网搜索完成，正在分析..."
    assert WEB_BODY_LIMIT == 2000
    # 关键不变量：web 兜底文案必须不在拒答标记里，否则 format_node 会误删引用
    assert (
        SSEInteractionTexts.WEB_SEARCH_PHRASE
        not in SSEInteractionTexts.ABSTENTION_MARKERS
    )
    assert "未在文档中找到" in SSEInteractionTexts.ABSTENTION_MARKERS
