"""测试 PromptManager 的兜底 prompt 经加载入口取自模板。"""

from src.config.prompts import loader
from src.infra.llm.prompt_manager import (
    _FALLBACK_SYSTEM_PROMPT,
    _FALLBACK_USER_TEMPLATE,
)


def test_fallback_system_imported_from_config():
    """_FALLBACK_SYSTEM_PROMPT 应以基础段 + 委派引导段为前缀。"""
    prefix = loader.get_content("base-financial") + loader.get_content(
        "tools-delegate-guidance"
    )
    assert _FALLBACK_SYSTEM_PROMPT.startswith(prefix)


def test_fallback_system_has_citation():
    """_FALLBACK_SYSTEM_PROMPT 应包含引用指令。"""
    assert loader.get_content("output-inline-citation") in _FALLBACK_SYSTEM_PROMPT


def test_fallback_user_imported_from_config():
    """_FALLBACK_USER_TEMPLATE 应与用户消息模板完全相同。"""
    assert _FALLBACK_USER_TEMPLATE == loader.get_content("task-user-prompt")


def test_fallback_system_not_empty():
    assert len(_FALLBACK_SYSTEM_PROMPT) > 100


def test_fallback_user_not_empty():
    assert len(_FALLBACK_USER_TEMPLATE) > 50
