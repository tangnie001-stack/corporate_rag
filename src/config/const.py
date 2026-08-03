"""诊断日志标签、业务常量。"""


class _Labels(dict):
    """标签字典，get() 无值时返回空字符串。"""

    def get(self, key, *args):
        return super().get(key, args[0] if args else "")


# ── 日志标签字典 ──
ROUTE_LABELS = _Labels(
    {
        "simple": "skip_retrieval",
        "medium": "go_to_rewrite",
        "complex": "go_to_rewrite",
    }
)

GENERATE_LABELS = _Labels(
    {
        True: "fallback_to_naive_rag",
        False: "enhanced_rag",
    }
)


# ── abstention 状态提示 ──
# agent_service 在拒答（直接返回静态文案）时发送
ABSTENTION_STATUS_MSG: str = "未找到相关文档，已直接答复"
