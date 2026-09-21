"""五个任务模板的占位符必须被其消费点全部提供（切换 render 后的新失败形态防护）。

`loader.render` 对未提供的占位符原样保留（不抛错），所以"漏传变量"不再表现为
每请求 500，而是"花括号字段名原样进入 prompt"。本测试把这条静默失败变成显式断言。
"""

import pathlib
import re

import pytest

from src.config.prompts import loader

_PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")

# 模板 id → 该模板消费点实际传入的变量集合（与各消费点代码逐处核对一致）
_CONSUMER_VARIABLES: dict[str, set[str]] = {
    "task-user-prompt": {"context", "query"},
    "task-classifier-system": set(),
    "task-classifier-user": {
        "query",
        "entities",
        "kb_entities",
        "complexity_score",
        "history",
    },
    "task-rewrite-system": set(),
    "task-rewrite-user": {"query", "route", "history"},
    "task-entity-system": set(),
    "task-entity-user": {
        "filename",
        "heading_tree",
        "text_prefix",
        "rule_candidates",
    },
}


@pytest.mark.parametrize("template_id", sorted(_CONSUMER_VARIABLES))
def test_template_placeholders_are_all_provided(template_id: str) -> None:
    """模板引用的占位符 ⊆ 消费点提供的变量集合。"""
    content = loader.get_content(template_id)
    used = set(_PLACEHOLDER.findall(content))
    provided = _CONSUMER_VARIABLES[template_id]
    missing = used - provided
    assert not missing, (
        f"{template_id} 引用了消费点未提供的占位符 {missing}；"
        f"切换 loader.render 后它们会原样进入 prompt"
    )


def test_render_keeps_unknown_placeholder_verbatim() -> None:
    """未提供的占位符原样保留（与 str.format 抛 KeyError 的语义差异）。"""
    assert loader.render("a {known} b {unknown}", {"known": "K"}) == "a K b {unknown}"


def test_no_str_format_on_templates_in_consumers() -> None:
    """消费点不得对模板再调用 str.format（二次替换是双入口的根源）。"""
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    for rel in (
        "src/infra/search/query_router.py",
        "src/infra/search/document_entity_extractor.py",
        "src/infra/llm/prompt_manager.py",
    ):
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert ".format(" not in text, f"{rel} 仍在对模板调用 str.format"


def test_compare_rewrite_render_exception_is_scoped() -> None:
    """compare_rewrite.py 的 user_template 走 loader.render，且例外被收窄。

    例外：该文件刻意保留 INDEPENDENT_REWRITE_USER_TEMPLATE.format(...) —— 它是
    "独立改写 vs 捆绑改写" 对比实验的另一边（冻结副本，不在本变更范围），因此这里
    不做文件级 ``".format(" not in text`` 断言，只钉住 user_template 已切到 loader.render。
    """
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    text = (repo_root / "src/cli/compare_rewrite.py").read_text(encoding="utf-8")
    assert "loader.render(" in text, (
        "compare_rewrite.py 的 user_template 未走 loader.render"
    )
    assert "user_template.format(" not in text, (
        "compare_rewrite.py 仍在 format user_template"
    )
    # 冻结的对比副本仍在用 str.format —— 刻意保留，不得随本变更移除
    assert "INDEPENDENT_REWRITE_USER_TEMPLATE.format(" in text


@pytest.mark.parametrize(
    ("template_id", "variables"),
    [
        (
            "task-rewrite-user",
            {"query": "Q", "route": "medium", "history": "H"},
        ),
        (
            "task-entity-user",
            {
                "filename": "f.pdf",
                "heading_tree": "H",
                "text_prefix": "T",
                "rule_candidates": "{}",
            },
        ),
    ],
)
def test_rendered_json_example_has_no_format_escapes(
    template_id: str, variables: dict[str, str]
) -> None:
    """模板不得带 str.format 时代的双花括号转义。

    根因：渲染已统一走 loader.render（不做 {{ }} → { } 还原），残留的双花括号会
    原样进入 prompt，破坏"严格按此格式输出 JSON"示例。
    """
    rendered = loader.render(loader.get_content(template_id), variables)
    assert "{{" not in rendered, (
        f"{template_id} 渲染后仍含 {{{{ —— 模板不得带 str.format 时代的双花括号转义，"
        f"因为渲染已统一走 loader.render（不做还原）"
    )
    assert "}}" not in rendered, (
        f"{template_id} 渲染后仍含 }}}} —— 模板不得带 str.format 时代的双花括号转义，"
        f"因为渲染已统一走 loader.render（不做还原）"
    )
    for name in variables:
        assert f"{{{name}}}" not in rendered, (
            f"{template_id} 渲染后仍有未替换的 {{{name}}}"
        )
