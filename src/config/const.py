"""诊断日志标签、业务常量。"""

from enum import Enum
from typing import ClassVar


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
MAX_VERIFY_ASK_PER_TURN = (
    1  # verify"是否联网"询问每轮上限（独立计数，不计入 MAX_ASK_PER_TURN）
)
# verify 修订保险丝上限：正常被决策化（看 agent 上一轮 search_web queries）提前终止，
# 仅在 agent 反复不按指引执行时兜底防死循环
MAX_VERIFY_REGENERATIONS = 2
# verify 联网指引 SystemMessage 的标记短语：注入与查重共用（避免裸字符串耦合，
# 注入文案改了而查重漏改会破坏防重复注入逻辑）
VERIFY_GUIDANCE_MARKER: str = "用户已确认联网"
# verify "一次带全" hint SystemMessage 的标记短语：完整指引已注入后 agent 上一轮
# search_web queries 仍带漏缺失年份时，独立补发一条带全提示（重申轮也须送达，
# 是"还缺哪些年 + 一次带全再查"的新信息；注入与查重共用此短语防重复堆叠）
VERIFY_HINT_MARKER: str = "一次带全以下年份"
# verify 联网引用标注指引 SystemMessage 的标记短语（无 KB 场景，2026-09-02）：
# 本轮调过 search_web 但回答未带 [n] 来源编号时注入，驱动 agent 补标注后重生成一次
VERIFY_CITATION_MARKER: str = "请为联网引用标注来源编号"
# verify KB 强制溯源指引 SystemMessage 的标记短语（态 B）：检索到 KB context 但答案无
# [n] 时注入，驱动 agent 补标后重生成一次（完整性通过后、judge 前，只引导一次）
VERIFY_KB_CITATION_MARKER: str = "请为知识库引用标注来源编号"
ASK_USER_TIMEOUT = 120  # ask_user 等待用户回答超时秒数
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数
HISTORY_TOKEN_RATIO = 0.3  # 历史 token 占 context 窗口上限比例
# per-session 并发锁 TTL 秒：须大于 ASK_USER_TIMEOUT（ask_user 挂起等待期间锁不能提前
# 过期，否则并发兜底失效），在超时基础上留 60s 余量
SESSION_LOCK_TTL = ASK_USER_TIMEOUT + 60
# 候选年份并入的最近完整年度数（排除进行中的当年）：KB 只覆盖 2024 时"这几年"仍能
# 解析出 [2023, 2025] 等缺失年份触发联网询问
TEMPORAL_RECENT_N_YEARS = 3

# ── delegate（主从委派）护栏常量 ──
# 来源：agent-delegation-skills change（fork 超时/结果截断/inline 规模约束）+
# delegate-hardening-observability change（三层防失控：事件级空闲 / 总时长保险丝 / turn 上限）
MAX_DELEGATE_BONUS = (
    2  # delegate 轮后主 agent 迭代上限放宽轮数（整合余量，单请求总上限仍封顶）
)
DELEGATE_DEFAULT_MAX_TURNS = (
    5  # fork 零工具默认 turn 上限（防御；开放工具后由 skill max_iterations 覆盖）
)
DELEGATE_RESULT_LIMIT = 1000  # fork 结果回流主 agent 的截断阈值（字符）
INLINE_PROMPT_MAX_CHARS = (
    500  # inline skill 正文规模上限（字符，防上下文累积膨胀，超出仅记 warning）
)
# 专家分析标记短语：fork 子代理"无源分析观点"由 4.1 引导主 agent 措辞（design D9），
# kb_citation_guardrail 据此豁免（防纯分析型 fork 答案被误触发补标 regen，M7）
EXPERT_ANALYSIS_MARKER = "基于领域经验的分析"


class DelegateStopReason(str, Enum):
    """fork 结束原因统一枚举（delegate end ok/reason 与 task 注册表终态共用词表）。

    取值：normal=正常完成 / idle=流空闲超时 / total=总时长保险丝 /
    turn=轮次上限 / failed=执行异常 / cancelled=请求取消。
    契约/文档/前端文案不得另起原因词（spec delegate-execution-controls）。
    """

    NORMAL = "normal"
    IDLE = "idle"
    TOTAL = "total"
    TURN = "turn"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType:
    """任务看板条目类型（task-board change）。

    分工：plan = 主 agent 经 Task 工具创建的计划/跟踪项；
    execution = delegate fork 自动登记的执行追踪（task_id=delegate_id）。
    """

    PLAN: str = "plan"  # 主 agent 计划项
    EXECUTION: str = "execution"  # delegate 执行追踪


class TaskStatus:
    """任务条目的展示状态（前端胶囊/徽标依据；中断原因另存 reason）。

    终态映射（execution）：reason=normal → done；failed → failed；
    idle/total/turn → timeout（reason 保留具体枚举值）；cancelled → cancelled。
    """

    PENDING: str = "pending"  # 待处理（plan 建项默认）
    RUNNING: str = "running"  # 进行中（execution 运行中 / plan 手动置）
    DONE: str = "done"  # 完成
    FAILED: str = "failed"  # 失败
    TIMEOUT: str = "timeout"  # 超时中断（idle/total/turn）
    CANCELLED: str = "cancelled"  # 取消


# execution 自动登记标题模板（task-board）：{skill} 为命中 skill 名；
# 放 const 集中管理（CLAUDE.md 硬编码集中规则），delegate_task 登记用
DELEGATE_TASK_TITLE_TMPL = "{skill} · 领域专家分析"


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

    # ── 缓冲续传（_subscribe_buffer）──
    # tail 空闲超时文案：超过 max_idle 无新事件时以 error 事件返回，提示刷新页面
    RESUME_TIMEOUT_TEXT: str = "续传超时，请刷新页面"

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

    # ── delegate_task 工具返回主 agent 的文本 ──
    # 未知 skill 返回模板：{skill}=请求的 skill 名；{available}=可用 skill 列表（空列表显示"无"）
    DELEGATE_UNKNOWN_SKILL: str = "skill 不存在: {skill}，可用 skill: {available}"
    # fork 超时文案（delegate_task 返回给 LLM，促其基于现有上下文作答）
    DELEGATE_TIMEOUT_TEXT: str = "Error: 领域专家分析超时，请基于已有检索上下文作答"
    # fork 中断/委派终态文案：中断时以 {reason} 填 DELEGATE_REASON_TEXT 的中文短词
    DELEGATE_INTERRUPT_TEXT: str = "领域专家分析中断 · {reason}"
    # DelegateStopReason → 前端可读中文短词（双通道：文字 + 颜色）
    DELEGATE_REASON_TEXT: ClassVar[dict[str, str]] = {
        "idle": "空闲超时",
        "total": "超时",
        "turn": "轮次上限",
        "failed": "失败",
        "cancelled": "已取消",
    }
    # fork 结果截断前缀模板：{total}=完整字数；{truncated}=截断后的摘要文本
    DELEGATE_TRUNCATED_PREFIX: str = (
        "子代理已产出完整分析 {total} 字，摘要如下：\n{truncated}"
    )


# ── 来源权威分级（source-tier-labeling change）──
# KB 内部文档固定档：resolve_source_tier 对 kind=kb 返回，不走域名解析
SOURCE_TIER_KB: int = 0
# web 未命中清单与模式时的默认中性档（非差评）
SOURCE_TIER_DEFAULT: int = 3
# 规则域 → 档位（人工维护先验；增补走候选规则审核流程，见 cookbook）。
# 匹配语义：域边界后缀（== 或 endswith("." + rule)），最长后缀优先
SOURCE_TIER_RULES: dict[str, int] = {
    # T1 官方一手
    "tencent.com": 1,
    "cninfo.com.cn": 1,
    "sse.com.cn": 1,
    "szse.cn": 1,
    # T2 权威财经媒体
    "caixin.com": 2,
    "yicai.com": 2,
    "wallstreetcn.com": 2,
    "sina.com.cn": 2,
    "finance.sina.com.cn": 2,
    "eastmoney.com": 2,
    # T4 UGC
    "zhihu.com": 4,
    "xueqiu.com": 4,
    "weibo.com": 4,
    "guba.eastmoney.com": 4,
}
# 官方/教育机构域名模式：清单未命中时命中模式升 T1
SOURCE_TIER_PATTERN_SUFFIXES: tuple[str, ...] = (".gov.cn", ".edu.cn")
# 档位 → 中文标签（权威映射唯一来源；api_contract.md 同步登记，前端照抄勿另造文案，
# 改动动线：const.py → api_contract.md → chat.html 三步走完才算改完）
SOURCE_TIER_LABELS: dict[int, str] = {
    0: "内部文档",
    1: "官方一手",
    2: "权威媒体",
    3: "一般",
    4: "UGC",
}


def _extract_domain(url: str) -> str:
    """从 url 提取归一化域名。

    去 scheme/路径/query/userinfo/端口与 www. 前缀，转小写。

    Args:
        url: 来源 url

    Returns:
        归一化后的域名字符串
    """
    host = url.strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]  # 剥路径与 query
    host = host.split("@")[-1]  # 剥 userinfo
    host = host.rsplit(":", 1)[0]  # 剥端口
    host = host.removeprefix("www.")  # 剥 www. 前缀
    return host


def resolve_source_tier(url: str, kind: str) -> int:
    """来源权威确定性定档。

    KB kind 固定 T0；web 按规则域匹配（域边界后缀、最长后缀优先），
    官方/教育域名模式升 T1，未命中默认中性档 T3。同一 url 恒定输出，
    不随模型采样变化（模型判断不改写档位，design D1）。

    Args:
        url: 来源 url（KB 来源可为文件名，不参与解析）
        kind: 来源类型（SSEInteractionTexts.CITATION_KIND_KB / CITATION_KIND_WEB）

    Returns:
        档位整数（0=内部文档 1=官方一手 2=权威媒体 3=一般 4=UGC）
    """
    if kind == SSEInteractionTexts.CITATION_KIND_KB:
        return SOURCE_TIER_KB
    domain = _extract_domain(url)
    matches = [
        rule
        for rule, tier in SOURCE_TIER_RULES.items()
        if domain == rule or domain.endswith("." + rule)
    ]
    if matches:
        # 最长后缀优先：guba.eastmoney.com(T4) 覆盖 eastmoney.com(T2)
        best = max(matches, key=len)
        return SOURCE_TIER_RULES[best]
    for suffix in SOURCE_TIER_PATTERN_SUFFIXES:
        if domain.endswith(suffix):
            return 1
    return SOURCE_TIER_DEFAULT
