"""测试 PromptManager 的兜底 prompt 从 src.config.prompts 正确导入。"""

from src.infra.llm.prompt_manager import (
    _FALLBACK_SYSTEM_PROMPT,
    _FALLBACK_USER_TEMPLATE,
    _INLINE_CITATION_INSTRUCTION,
)
from src.config.prompts import FINANCIAL_SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


def test_fallback_system_imported_from_config():
    """_FALLBACK_SYSTEM_PROMPT 应以 FINANCIAL_SYSTEM_PROMPT 为前缀。"""
    assert _FALLBACK_SYSTEM_PROMPT.startswith(FINANCIAL_SYSTEM_PROMPT)


def test_fallback_system_has_citation():
    """_FALLBACK_SYSTEM_PROMPT 应包含引用指令。"""
    assert _INLINE_CITATION_INSTRUCTION in _FALLBACK_SYSTEM_PROMPT


def test_fallback_user_imported_from_config():
    """_FALLBACK_USER_TEMPLATE 应与 USER_PROMPT_TEMPLATE 完全相同。"""
    assert _FALLBACK_USER_TEMPLATE == USER_PROMPT_TEMPLATE


def test_fallback_system_not_empty():
    assert len(_FALLBACK_SYSTEM_PROMPT) > 100


def test_fallback_user_not_empty():
    assert len(_FALLBACK_USER_TEMPLATE) > 50
