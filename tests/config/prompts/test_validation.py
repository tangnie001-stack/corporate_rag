"""启动期校验：base 段非空、general 存在、总字符数事故兜底。

失败语义见 `src/config/prompts/validation.py` 顶部：任一校验失败即抛
`TemplateLoadError`，由启动流程透传，进程启动失败。
"""

import pytest

from src.config.prompts import loader, validation


def test_validate_all_passes_on_current_templates() -> None:
    """当前模板集合通过校验，并返回各段字符数。"""
    section_chars = validation.validate_all()
    assert set(section_chars) >= {"base", "sources", "output", "tools"}
    assert all(isinstance(v, int) and v > 0 for v in section_chars.values())


def test_validate_all_rejects_missing_general_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少 domain=general 的 base 模板时校验失败（默认值自身非法）。"""
    original = loader.load_all()

    def fake_load_all() -> dict[str, loader.Template]:
        """返回去掉 general 领域的模板映射。"""
        return {k: v for k, v in original.items() if v.domain != "general"}

    monkeypatch.setattr(loader, "load_all", fake_load_all)
    with pytest.raises(loader.TemplateLoadError):
        validation.validate_all()


def test_validate_all_rejects_oversized_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """总字符数超过事故兜底上限时校验失败。"""
    monkeypatch.setattr(validation, "SECTION_CHARS_LIMIT", 10)
    with pytest.raises(loader.TemplateLoadError):
        validation.validate_all()
