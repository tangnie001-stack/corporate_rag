"""跨轮历史摘要核心 —— 提示词组装、调用、四条不变量校验、编号剥离。

只做"把一段历史压成摘要"的纯逻辑与一次 LLM 调用，不读写存储、不碰并发锁
（存储归 `ChatManager`，编排归回合收尾处）。任何失败都返回 `degraded` 结果，
绝不外抛——调用方据此回退到纯裁剪。

本模块**不记日志**：降级信号由编排层（`summary_scheduler`）按 `degraded` 与
`reason` 统一落 `[session]` 事件，避免同一失败在两处重复记录、也避免本模块
依赖事件注册表（事件在后续任务才登记）。
"""

import asyncio
import re
from dataclasses import dataclass

from src.config.const import SUMMARY_TIMEOUT_S
from src.config.prompts import loader
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens
from src.models import get_summary_llm

SUMMARY_SYSTEM_TEMPLATE_ID = "task-summary-system"

# 只匹配方括号内的 1-2 位数字（引用编号）；不碰年份、Markdown 链接等。
_CITATION_RE = re.compile(r"\[\d{1,2}\]")


@dataclass(frozen=True)
class SummaryResult:
    """一次摘要尝试的结果。"""

    text: str  # 摘要正文；degraded 时为空串
    covered: int  # 摘要覆盖到的消息条数 = 被丢弃段条数
    degraded: bool  # 是否降级（未采用摘要）
    reason: str  # 降级原因；成功时为空串


def strip_citation_numbers(text: str) -> str:
    """剥离正文中的 `[数字]` 引用编号（不因编号丢弃整篇摘要）。"""
    return _CITATION_RE.sub("", text)


def validate_summary(
    text: str, discarded_tokens: int, previous_tokens: int | None
) -> tuple[bool, str]:
    """校验四条不变量中可离线判定的两条（更小 / 严格不增长）。

    Args:
        text: 候选摘要正文
        discarded_tokens: 被丢弃段的 token 量
        previous_tokens: 上一版摘要的 token 量；None 表示尚无上一版

    Returns:
        (是否采纳, 不采纳原因)；原因为空串表示通过
    """
    if not text.strip():
        return False, "empty"
    tokens = count_tokens(text)
    if tokens >= discarded_tokens:
        return False, "not_smaller"
    if previous_tokens is not None and tokens > previous_tokens:
        return False, "grew"
    return True, ""


def _render_discarded(discarded: list[ChatMessage]) -> str:
    """把被丢弃段渲染为摘要输入文本（按角色标注）。"""
    lines: list[str] = []
    for message in discarded:
        if message.role == "user":
            lines.append(f"[用户] {message.content}")
        else:
            lines.append(f"[助手] {message.content}")
    return "\n".join(lines)


async def summarize_history(
    previous_text: str, discarded: list[ChatMessage]
) -> SummaryResult:
    """生成/更新摘要；任何失败都返回 degraded 结果，绝不外抛。

    被丢弃段是**累计前缀**（`split_history_window` 每次返回保留尾部之外的
    全部更旧段），本次摘要覆盖的正是这整个前缀，故 `covered = len(discarded)`。
    不叠加上一版覆盖数——那会把同一批更旧消息重复计数（`covered` 是写进
    Redis 的持久字段，膨胀值会随会话累积）。

    Args:
        previous_text: 上一版摘要正文（无则空串）
        discarded: 本次要压入摘要的更旧消息段（非空，为累计前缀）

    Returns:
        SummaryResult；degraded=True 时 text 为空、covered 记本次被丢弃段条数
    """
    covered = len(discarded)
    # 取模板、拼输入、统计被丢弃段 token 一并纳入降级边界：模板缺失（`get_content`
    # 抛 `KeyError`）或返回非 str 时也必须走 degraded，绝不穿透本函数。
    try:
        system_prompt = loader.get_content(SUMMARY_SYSTEM_TEMPLATE_ID)
        body = _render_discarded(discarded)
        if previous_text:
            user_prompt = (
                f"上一版摘要：\n{previous_text}\n\n本次要并入的更早对话：\n{body}"
            )
        else:
            user_prompt = f"要压缩的更早对话：\n{body}"
        previous_tokens = None
        if previous_text:
            previous_tokens = count_tokens(previous_text)
        discarded_tokens = sum(count_tokens(m.content) for m in discarded)
        llm = get_summary_llm()
        response = await asyncio.wait_for(
            llm.ainvoke(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            ),
            timeout=SUMMARY_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        return SummaryResult("", covered, True, f"llm_error: {exc}")
    # 不变量③「拒绝截断摘要」：结束原因为长度截断即视为失败，不采用半截摘要。
    # 该判据成立的前提是 `get_summary_llm()` 已设显式 max_tokens。
    metadata = response.response_metadata
    finish_reason = ""
    if isinstance(metadata, dict):
        finish_reason = str(metadata.get("finish_reason", ""))
    if finish_reason == "length":
        return SummaryResult("", covered, True, "truncated")
    text = strip_citation_numbers(str(response.content))
    ok, reason = validate_summary(
        text,
        discarded_tokens=discarded_tokens,
        previous_tokens=previous_tokens,
    )
    if not ok:
        return SummaryResult("", covered, True, reason)
    return SummaryResult(text, covered, False, "")
