"""19 条模板文件可解析、字段合法、id 唯一。"""

from pathlib import Path

import pytest
import yaml

TEMPLATES_DIR = (
    Path(__file__).resolve().parents[3] / "src" / "config" / "prompts" / "templates"
)
VALID_SECTIONS = {"base", "runtime_contract", "sources", "tools", "output"}


def _all_templates() -> list[dict]:
    """汇总所有模板文件里的模板条目。"""
    items: list[dict] = []
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} 根节点不是映射"
        assert "templates" in data, f"{path.name} 缺少 templates 根键"
        for entry in data["templates"]:
            items.append({"_file": path.name, **entry})
    return items


def test_template_count_and_migrated_ids() -> None:
    """19 条 = 12 条搬运 + 1 条通用 base（base-general）+ 6 条新增段模板（runtime-contract / sources-general / sources-kb-web-rules / output-delegate-citation / tools-execution / tools-ask-user）。"""
    templates = _all_templates()
    assert len(templates) == 19
    migrated = {
        "tools-delegate",
        "base-financial",
        "sources-kb-unbound",
        "sources-kb-ladder",
        "task-user-prompt",
        "task-classifier-system",
        "task-classifier-user",
        "task-rewrite-system",
        "task-rewrite-user",
        "task-entity-system",
        "task-entity-user",
        "output-citation",
    }
    assert migrated <= {t["id"] for t in templates}, "12 条搬运模板必须齐全"


def test_ids_are_unique() -> None:
    """id 全局唯一。"""
    ids = [t["id"] for t in _all_templates()]
    assert len(ids) == len(set(ids))


def test_section_templates_have_valid_section() -> None:
    """kind=section 的模板，section 取值在允许集合内。"""
    for t in _all_templates():
        if t["kind"] == "section":
            assert t["section"] in VALID_SECTIONS, t
        else:
            assert t["kind"] == "task", t
            assert "section" not in t, f"task 模板不应有 section：{t['id']}"


def test_base_templates_have_domain() -> None:
    """section=base 的模板必须声明 domain；通用 base 用保留值 general。"""
    for t in _all_templates():
        if t.get("section") == "base":
            assert t.get("domain"), f"{t['id']} 缺少 domain"


@pytest.mark.parametrize("tid", ["base-financial"])
def test_content_is_non_empty(tid: str) -> None:
    """段模板正文非空。"""
    entry = next(t for t in _all_templates() if t["id"] == tid)
    assert entry["content"].strip()


def test_runtime_contract_segment_exists_and_is_unconditional() -> None:
    """runtime_contract 段存在，且含数据·指令边界与完成条件两条无条件规则。"""
    from src.config.prompts import loader

    template = loader.load_all()["runtime-contract"]
    assert template.kind == "section"
    assert template.section == "runtime_contract"
    assert template.domain is None
    content = template.content
    assert "不可信的来源数据" in content, "缺数据·指令边界"
    assert "停止调用工具" in content, "缺完成条件"
    assert "仅有进度更新不算完成任务" in content, "完成条件措辞不完整"


def test_output_segment_templates_exist() -> None:
    """output 段两条模板的归属字段正确，引用编码一条为无条件。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    citation = templates["output-citation"]
    assert citation.kind == "section"
    assert citation.section == "output"
    assert "[n]" in citation.content or "[1][2]" in citation.content

    delegated = templates["output-delegate-citation"]
    assert delegated.section == "output"
    assert "基于领域经验的分析" in delegated.content, (
        "EXPERT_ANALYSIS_MARKER 短语必须逐字保留：kb_citation_guardrail 靠它豁免"
    )


def test_old_output_inline_citation_template_removed() -> None:
    """旧模板 id 已删除（一条事实一个 owner，不留并存副本）。"""
    from src.config.prompts import loader

    assert "output-inline-citation" not in loader.load_all()


def test_tools_segment_has_three_templates() -> None:
    """tools 段三条模板：通用执行（无条件）+ ask_user + delegate，归属字段正确。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    for tid in ("tools-execution", "tools-ask-user", "tools-delegate"):
        template = templates[tid]
        assert template.kind == "section", tid
        assert template.section == "tools", tid

    assert "ask_user" in templates["tools-ask-user"].content
    assert "delegate_task" in templates["tools-delegate"].content


def test_ask_user_rule_not_in_sources() -> None:
    """澄清时机不写在来源段（spec「澄清时机不写在来源段」）。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    sources_text = "\n".join(
        t.content for t in templates.values() if t.section == "sources"
    )
    assert "ask_user" not in sources_text


def test_delegate_guidance_old_template_removed() -> None:
    """旧 tools-delegate-guidance 已删除，不留并存副本。"""
    from src.config.prompts import loader

    assert "tools-delegate-guidance" not in loader.load_all()


def test_sources_segment_templates_and_ownership() -> None:
    """sources 段三条工具相关模板归属正确；检索阶梯归本段、不在 base。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    for tid in ("sources-general", "sources-kb-ladder", "sources-kb-web-rules"):
        template = templates[tid]
        assert template.kind == "section", tid
        assert template.section == "sources", tid

    ladder = templates["sources-kb-ladder"].content
    assert "换一种问法" in ladder
    assert "top_k=10" in ladder
    assert "至少一个核心实体" in ladder

    web = templates["sources-kb-web-rules"].content
    assert "该问题不在当前知识库范围内" in web, "漂移拷贝删掉的出口指引必须补回"
    assert "未在文档中找到相关数据" in web
    assert "不要调用 search_web" in web


def test_drift_copy_template_removed() -> None:
    """漂移拷贝 sources-kb-bound-discipline 已删除。"""
    from src.config.prompts import loader

    assert "sources-kb-bound-discipline" not in loader.load_all()


def test_evidence_sufficiency_stop_not_in_p1() -> None:
    """P1 的 sources 段不得出现"证据足够即停止检索"（属 P2，task 3.3）。"""
    from src.config.prompts import loader

    templates = loader.load_all()
    sources_text = "\n".join(
        t.content for t in templates.values() if t.section == "sources"
    )
    assert "证据足够即停止检索" not in sources_text
