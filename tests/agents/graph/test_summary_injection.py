"""摘要段注入的单测：位置、让位声明、聚合预算上限、无摘要时零影响。"""

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.graph import agent_node
from src.agents.graph.agent_node import _SUMMARY_TAG, _compose_summary_message
from src.config.const import HISTORY_TOKEN_BUDGET
from src.infra.llm.token_count import count_tokens


def test_compose_summary_message_has_tag_and_yield_clause():
    """摘要段带标签与让位声明（事实以本轮检索为准 + 不确定时请用户复述）。"""
    message = _compose_summary_message("## 用户目标\n看年报")
    assert isinstance(message, HumanMessage)
    assert _SUMMARY_TAG in message.content
    assert "以本轮检索结果为准" in message.content
    assert "请用户复述" in message.content


def test_compose_summary_message_strips_citation_numbers():
    """注入前剥离 [数字] 编号（防 format 阶段映射串号）。"""
    message = _compose_summary_message("见 [1] 与 [2]")
    assert "[1]" not in message.content
    assert "[2]" not in message.content


def test_summary_message_inserted_after_last_system_message():
    """摘要段插到最后一个 SystemMessage 之后、普通历史之前。"""
    messages = [
        SystemMessage(content="SYS-1"),
        SystemMessage(content="SYS-2"),
        HumanMessage(content="旧问题"),
        HumanMessage(content="当前问题"),
    ]
    out = agent_node._insert_after_last_system(
        messages, [_compose_summary_message("摘要")]
    )
    assert isinstance(out[2], HumanMessage)
    assert _SUMMARY_TAG in out[2].content
    assert out[3].content == "旧问题"


def test_summary_and_tail_fit_budget():
    """聚合预算：摘要段与尾部合计超 `HISTORY_TOKEN_BUDGET` 时先缩摘要。"""
    from src.agents.graph.agent_node import _fit_summary_to_budget

    tail = [HumanMessage(content="尾" * 3000)]
    # 摘要远大于「预算 − 尾部」的余量，强制触发截断（否则该用例恒真、测不到东西）
    over = "摘" * 20000
    fitted = _fit_summary_to_budget(over, tail)
    total = count_tokens(fitted) + sum(count_tokens(str(m.content)) for m in tail)
    assert total <= HISTORY_TOKEN_BUDGET
    assert len(fitted) < len(over)
