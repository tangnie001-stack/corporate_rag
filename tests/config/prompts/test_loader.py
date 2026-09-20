"""加载入口：读取、按段查询、占位符规则、失败语义。"""

from pathlib import Path

import pytest

from src.config.prompts import loader


def test_get_content_returns_verbatim_template() -> None:
    """取到的正文与模板文件里的一致（首尾空白不丢）。"""
    content = loader.get_content("sources-kb-bound-discipline")
    assert content.startswith("\n\n检索纪律：")
    assert content.endswith("范围内。")


def test_get_content_unknown_id_raises() -> None:
    """未知 id 抛 KeyError，不返回空串。"""
    with pytest.raises(KeyError):
        loader.get_content("no-such-template")


def test_render_substitutes_known_placeholder() -> None:
    """已知占位符被替换。"""
    text = loader.render("你好 {name}", {"name": "世界"})
    assert text == "你好 世界"


def test_render_keeps_unknown_placeholder_verbatim() -> None:
    """未提供的占位符原样保留，不被替换为空串（spec <prompt-carrier>）。"""
    text = loader.render("你好 {name}，时间 {current_time}", {"name": "世界"})
    assert text == "你好 世界，时间 {current_time}"


def test_render_does_not_evaluate_expressions() -> None:
    """表达式写法当普通文本原样输出，不求值。"""
    text = loader.render("{% if x %}a{% endif %}", {})
    assert text == "{% if x %}a{% endif %}"


def test_render_keeps_double_brace_expression_verbatim() -> None:
    """{{name}} 这类表达式写法整体原样输出，不发生部分替换。"""
    assert loader.render("{{name}}", {"name": "x"}) == "{{name}}"
    assert loader.render("{{ name }}", {"name": "x"}) == "{{ name }}"


def test_load_all_rejects_task_template_with_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kind=task 的条目不得带 section（绑定约束：task 模板不参与段归属）。"""
    (tmp_path / "task-with-section.yaml").write_text(
        "templates:\n"
        "  - id: task-x\n"
        "    kind: task\n"
        "    section: sources\n"
        '    content: "正文"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "TEMPLATES_DIR", tmp_path)
    loader.load_all.cache_clear()
    try:
        with pytest.raises(loader.TemplateLoadError):
            loader.load_all()
    finally:
        loader.load_all.cache_clear()


def test_get_by_section_returns_section_templates() -> None:
    """按段取模板，且不含 kind=task 的条目。"""
    items = loader.get_by_section("sources")
    ids = {t.id for t in items}
    assert "sources-kb-unbound" in ids
    assert all(t.kind == "section" for t in items)


def test_get_domain_base_returns_domain_text() -> None:
    """按领域取 base 正文。"""
    text = loader.get_domain_base("finance")
    assert text.startswith("你是一个智能问答助手")
