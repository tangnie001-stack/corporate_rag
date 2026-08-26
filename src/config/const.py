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


# ── 实体抽取常量 ──
# 核心实体类型：文档级属性，渲染进 prompt 支撑 faithfulness 锚点
ENTITY_TYPES: tuple[str, ...] = ("company", "report_period", "sec_code")
# 核心实体渲染顺序（to_prompt_text 按此顺序渲染存在的实体）
ENTITY_RENDER_ORDER: tuple[str, ...] = ("company", "report_period", "sec_code")
# 实体渲染中文标签（to_prompt_text 渲染实体时的展示名，未知键回退为原始键）
ENTITY_LABELS: dict[str, str] = {
    "company": "公司",
    "report_period": "期间",
    "sec_code": "代码",
}
# 可选实体：LLM 兜底顺带返回，仅补充字段不渲染
ENTITY_OPTIONAL_TYPES: tuple[str, ...] = ("person", "currency", "report_type")
# 实体抽取完整三层流水线的文件类型（其余如 txt 走文件名+LLM）
ENTITY_FULL_PIPELINE_TYPES: tuple[str, ...] = ("pdf", "docx")


# ── agent 循环护栏常量 ──
# 来源：agentic 改造需求（2026-08-26 phase1）；用途：agent 循环的迭代/追问/历史注入/并发控制
MAX_AGENT_ITERATIONS = 5  # agent 循环最大迭代数，超限强制收尾
MAX_ASK_PER_TURN = 2  # 单 turn 内 ask_user 最大调用次数
ASK_USER_TIMEOUT = 120  # ask_user 等待用户回答超时秒数
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数
HISTORY_TOKEN_RATIO = 0.3  # 历史 token 占 context 窗口上限比例
SESSION_LOCK_TTL = 30  # per-session 并发锁 TTL 秒
