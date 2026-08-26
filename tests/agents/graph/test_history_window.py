"""测试历史窗口截断 — _truncate_history 的轮数 + token 双上限。

直接构造 ChatMessage 调用模块函数，不发真实网络调用。
"""

from src.agents.graph.agent_node import _truncate_history
from src.infra.llm.chat_message import ChatMessage


def test_truncate_keeps_recent_turns():
    """15 轮历史 → 输出 ≤ 20 条（最近 10 轮），最后一条是最近的 assistant 消息。"""
    history = []
    for i in range(15):
        history.append(ChatMessage(role="user", content=f"q{i}"))
        history.append(ChatMessage(role="assistant", content=f"a{i}"))

    out = _truncate_history(history, max_turns=10, token_ratio=0.3, context_window=8000)

    assert len(out) <= 20  # 10 轮 * 2 条
    assert out[-1].content == "a14"  # 最近一条保留


def test_token_ratio_truncates_oldest():
    """长消息导致总 token 超预算（8000*0.3=2400）时，从最旧截断、条数减少。"""
    history = []
    for _ in range(5):
        history.append(ChatMessage(role="user", content="x" * 2000))
        history.append(ChatMessage(role="assistant", content="y" * 2000))

    out = _truncate_history(history, max_turns=10, token_ratio=0.3, context_window=8000)

    # 每条约 1000 token，10 条共 10000，需弹到 2 条（2000 ≤ 2400）
    assert len(out) < 10
    assert len(out) == 2


def test_recent_round_always_kept():
    """极端长预算（总 token 远超上限）下最后 2 条仍完整保留。"""
    history = []
    for i in range(3):
        history.append(ChatMessage(role="user", content=f"q{i}" * 2000))
        history.append(ChatMessage(role="assistant", content=f"a{i}" * 2000))

    out = _truncate_history(history, max_turns=10, token_ratio=0.3, context_window=100)

    assert len(out) == 2  # 最近 1 轮（2 条）不被截
    assert out[0].content == "q2" * 2000
    assert out[-1].content == "a2" * 2000
