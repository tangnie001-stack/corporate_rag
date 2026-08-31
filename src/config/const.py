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
# search_web extract 拉取的正文上限（字符），防长网页撑爆上下文窗口
WEB_BODY_LIMIT: int = 2000


# ── agent 循环护栏常量 ──
# 来源：agentic 改造需求（2026-08-26 phase1）；用途：agent 循环的迭代/追问/历史注入/并发控制
MAX_AGENT_ITERATIONS = 5  # agent 循环最大迭代数，超限强制收尾
MAX_ASK_PER_TURN = 2  # 单 turn 内 ask_user 最大调用次数
ASK_USER_TIMEOUT = 120  # ask_user 等待用户回答超时秒数
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数
HISTORY_TOKEN_RATIO = 0.3  # 历史 token 占 context 窗口上限比例
# per-session 并发锁 TTL 秒：须大于 ASK_USER_TIMEOUT（ask_user 挂起等待期间锁不能提前
# 过期，否则并发兜底失效），在超时基础上留 60s 余量
SESSION_LOCK_TTL = ASK_USER_TIMEOUT + 60


# ── 检索精排超时 ──
# Reranker 精排总超时秒数：rerank 为同步 HTTP 调用（dashscope 无默认超时），
# 在事件循环内直连会永久挂起阻塞整个 worker，故经 to_thread + wait_for 兜底；
# 超时后降级为检索原始顺序（raw-order fallback），避免空结果触发 abstain
RERANK_TIMEOUT = 5


# ── SSE 交互事件文案与 stage 标识 ──
# 来源：agentic 改造需求（2026-08-26 phase1）；用途：SSE 事件与用户的交互说明
# （展示文案 + stage 标识）统一收敛，供 agent_service / rag_tools / clarify / nodes 引用
class SSEInteractionTexts:
    """SSE 与用户交互相关的事件文案与 stage 标识常量。

    stage 标识：SSEStatusEvent.stage 字段取值，前端按 message 展示、不依赖 stage 分支；
    事件文案：SSE 事件直接展示给用户或返回给 LLM（ask_user 工具错误）的文本。
    """

    # ── Abstention / 拒答 ──
    # 拒答语检测关键词：回答命中任一关键词时，format_node 不输出引用
    ABSTENTION_MARKERS: tuple[str, ...] = ("未在文档中找到",)

    # ── Web 搜索兜底 ──
    # web 兜底回答文案：命中此文案但带 [n] 引用时保留引用（区别于纯拒答）
    WEB_SEARCH_PHRASE: str = "该问题不在当前知识库范围内"
    # search_web 达每轮限次提示（返回给 LLM，促其基于现有信息作答）
    WEB_SEARCH_LIMIT_TEXT: str = "Error: 已达本轮联网搜索上限，请基于现有信息作答"
    # 引用来源类型：RAGContext.kind 与 citation.kind 取值
    CITATION_KIND_KB: str = "kb"
    CITATION_KIND_WEB: str = "web"
    # SSEStatusEvent.stage：search_web 工具阶段（start/end 双文案）
    STAGE_WEB_SEARCH: str = "web_search"
    WEB_SEARCH_STATUS_START: str = "正在联网搜索..."
    WEB_SEARCH_STATUS_END: str = "联网搜索完成，正在分析..."

    # ── SSEStatusEvent.stage 标识 ──
    # on_chat_model_start（agent 节点）对应 stage：模型开始思考
    STAGE_AGENT: str = "agent"

    # on_tool_start/on_tool_end（retrieve_kb）对应 stage：检索中/完成
    STAGE_RETRIEVE: str = "retrieve"

    # ── Agent 状态事件文案 ──
    # on_chat_model_start（agent 节点）→ SSEStatusEvent(STAGE_AGENT)：模型开始思考
    AGENT_STATUS_THINKING: str = "正在思考..."

    # on_tool_start（retrieve_kb）→ SSEStatusEvent(STAGE_RETRIEVE)：开始检索
    AGENT_STATUS_RETRIEVING: str = "正在检索相关文档..."

    # on_tool_end（retrieve_kb）→ SSEStatusEvent(STAGE_RETRIEVE)：检索完成
    AGENT_STATUS_RETRIEVED: str = "检索完成，正在分析..."

    # ── SSE 错误事件文案 ──
    # SSEErrorEvent 统一错误前缀：_dual_stream（事件源异常）与 stream_chat（外层兜底）共用
    SSE_ERROR_PREFIX: str = "暂时无法回答："

    # ── ask_user 澄清工具文案 ──
    # ask_user 上下文不可用文案：current_request_ctx 未设置时直接返回给 LLM
    ASK_USER_CTX_UNAVAILABLE: str = "Error: 请求上下文不可用"

    # ask_user 达本回合询问上限文案：返回给 LLM 促其基于现有信息作答
    ASK_USER_LIMIT_REACHED: str = "Error: 已达本回合询问上限，请基于现有信息作答"

    # ask_user 等待期间答案 Future 被取消文案：POST 端取消挂起澄清时返回
    ASK_USER_ANSWER_CANCELLED: str = "Error: 等待用户回答被取消"

    # ask_user 等待期间请求被取消文案：abort 信号置位（客户端断开/取消）时返回
    ASK_USER_REQUEST_CANCELLED: str = "Error: 请求已取消"

    # ask_user 等待用户回答超时文案：超过 ASK_USER_TIMEOUT（const.py 秒数）未获答案时作为工具结果
    # 给 LLM，引导其基于已有上下文给出推荐方案（而非报错）
    ASK_USER_TIMEOUT_TEXT: str = (
        "（用户因超时未填写内容）请基于已有上下文给出推荐方案。"
    )

    # /chat/clarify-answer 404 文案：POST 解析挂起澄清时查无该 session 或 Future 已结束（超时/取消）
    CLARIFY_ANSWER_NOT_FOUND_TEXT: str = "该澄清问题已超时或不存在"
