"""断言测试进程内 tracing 被全局关停（D14）。

为什么需要它：`settings.py` 的 `LANGFUSE_*` 内置默认值指向真实 host 与 key，
一旦接线，任何走 `_run_generation` / `agent_model` 的用例都会构造真实客户端并
上报 —— 违反「测试 mock 外部依赖，不发起真实网络调用」。本文件把"关停已生效"
钉成可回归的契约。
"""

import os

from src.config import settings


def test_langfuse_env_var_set_before_import():
    """守护 conftest 顶部的 import 期环境变量 —— 这是主手段，也是唯一能被删掉而不被察觉的一环。

    下面两条断言都被 conftest 的 fixture 兜住：删掉 conftest 顶部那行
    `os.environ["LANGFUSE_ENABLE"] = "false"`，它们依然全绿。只有本断言会在
    无 `.env` 的环境（CI / 新 clone / worktree）暴露主手段的缺失（直接下标取值，
    键不存在即 KeyError）。
    """
    assert os.environ["LANGFUSE_ENABLE"] == "false"


def test_langfuse_disabled_in_tests():
    """测试进程内 LANGFUSE_ENABLE 必须为 False。"""
    assert settings.LANGFUSE_ENABLE is False


def test_langfuse_context_disabled_in_tests():
    """langfuse SDK 的装饰器上下文也必须处于 disabled。"""
    from langfuse.decorators import langfuse_context

    client = langfuse_context.client_instance
    assert client is not None
    assert client.enabled is False
