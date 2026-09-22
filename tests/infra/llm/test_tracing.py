"""接线基础设施单测 —— 不发网络。"""

import uuid

from src.infra.llm import tracing


def test_new_trace_id_shape():
    """生成的 id 形如 trace_<uuid4>。"""
    tid = tracing.new_trace_id()
    assert tid.startswith("trace_")
    uuid.UUID(tid.removeprefix("trace_"))


def test_valid_trace_id_accepts_normal_values():
    """常规值（含 CLI 的 eval_<hex>）判为合法。"""
    assert tracing.is_valid_trace_id("trace_1234-abcd_EF")
    assert tracing.is_valid_trace_id("eval_a1b2c3d4e5f6")
    assert tracing.is_valid_trace_id("a")


def test_valid_trace_id_rejects_illegal_values():
    """空串、空白、超长、带非法字符一律拒绝。"""
    assert not tracing.is_valid_trace_id("")
    assert not tracing.is_valid_trace_id("has space")
    assert not tracing.is_valid_trace_id("has/slash")
    assert not tracing.is_valid_trace_id("x" * 121)
    assert tracing.is_valid_trace_id("x" * 120)


def test_configure_tracing_follows_settings(monkeypatch):
    """开关取值来自 settings（调用时读取，而非导入时冻结）。"""
    from langfuse.decorators import langfuse_context

    from src.config import settings

    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", False)
    tracing.configure_tracing()
    assert langfuse_context.client_instance.enabled is False

    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", True)
    tracing.configure_tracing()
    assert langfuse_context.client_instance.enabled is True

    # 收尾：恢复测试期的关停状态
    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", False)
    tracing.configure_tracing()
    assert langfuse_context.client_instance.enabled is False
