"""PromptManager 已收窄为加载入口的门面：不持有正文副本、不触网。"""

from src.infra.llm.prompt_manager import PromptManager


def test_no_fallback_module_constants_remain() -> None:
    """模块内不得再存在 _FALLBACK_* 正文副本（唯一事实源 = 本地 YAML）。"""
    import src.infra.llm.prompt_manager as module

    for name in (
        "_FALLBACK_SYSTEM_PROMPT",
        "_FALLBACK_USER_TEMPLATE",
        "_FALLBACK_CLASSIFIER_SYSTEM",
        "_FALLBACK_CLASSIFIER_USER",
        "_INLINE_CITATION",
        "_DELEGATE_GUIDANCE",
    ):
        assert not hasattr(module, name), f"{name} 应已删除"


def test_remote_prompt_names_are_delisted() -> None:
    """远端名单出列（ADR-0010）：本地模板是唯一事实源。"""
    assert PromptManager.PROMPT_NAMES == {}


def test_get_system_prompt_retired() -> None:
    """追加职责已移交段组装器，旧方法退役（避免双入口重复注入）。"""
    assert not hasattr(PromptManager, "get_system_prompt")


def test_user_template_renders_placeholders() -> None:
    """用户模板渲染经加载入口的 render（未知占位符原样保留，不抛错）。"""
    pm = PromptManager()
    rendered = pm.get_user_template(context="材料", query="营收多少")
    assert "材料" in rendered
    assert "营收多少" in rendered
    assert "请根据以下文档内容回答问题" not in rendered, "P1 后用户模板已去策略化"


def test_classifier_prompt_renders_without_format_braces() -> None:
    """分类器 prompt 的占位符已全部替换，正文不再残留花括号字段名。"""
    pm = PromptManager()
    prompt = pm.get_classifier_prompt(
        query="问题", entities="实体", complexity_score=3.0, history="历史"
    )
    assert "问题" in prompt
    assert "{query}" not in prompt
    assert "{complexity_score}" not in prompt


def test_classifier_prompt_has_no_format_escapes() -> None:
    """分类器模板不得带 str.format 时代的双花括号转义。

    渲染已统一走 loader.render，它不做 ``{{`` → ``{`` 的还原，因此模板正文里的
    ``{{`` / ``}}`` 会原样发给模型（JSON 示例会被写成无效的 ``{{ ... }}``）；
    正确的模板形态是单花括号 JSON 示例。
    """
    pm = PromptManager()
    prompt = pm.get_classifier_prompt(
        query="问题", entities="实体", complexity_score=3.0, history="历史"
    )
    reason = (
        "模板不得带 str.format 时代的双花括号转义，"
        "因为渲染已统一走 loader.render（不做还原）"
    )
    assert "{{" not in prompt, reason
    assert "}}" not in prompt, reason
    assert '"route": "simple|medium|complex"' in prompt, (
        "应保留单花括号形态的 JSON 示例"
    )
