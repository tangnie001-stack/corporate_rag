"""断言测试进程内 tracing 被全局关停（D14）。

为什么需要它：`settings.py` 的 `LANGFUSE_*` 内置默认值指向真实 host 与 key，
一旦接线，任何走 `_run_generation` / `agent_model` 的用例都会构造真实客户端并
上报 —— 违反「测试 mock 外部依赖，不发起真实网络调用」。本文件把"关停已生效"
钉成可回归的契约。
"""

from src.config import settings


def test_langfuse_disabled_in_tests():
    """测试进程内 LANGFUSE_ENABLE 必须为 False。"""
    assert settings.LANGFUSE_ENABLE is False


def test_langfuse_context_disabled_in_tests():
    """langfuse SDK 的装饰器上下文也必须处于 disabled。"""
    from langfuse.decorators import langfuse_context

    client = langfuse_context.client_instance
    assert client is not None
    assert client.enabled is False
