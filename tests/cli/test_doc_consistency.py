"""测试文档防腐检查（src/cli/check_docs.py）— 防 docs/agents/* 引用漂移。

防腐哲学：文档对代码的"引用必须指向存在物"（单向校验）。本测试断言 error 档
为空——任何 docs/agents/ 文档引用了已删除/改名的代码路径或路由，pytest 即失败，
把"文档过时"变成可自动发现的信号（配合 pre-commit always_run hook 在提交前拦截）。
"""

from pathlib import Path

import pytest

from src.cli import check_docs
from src.cli.check_docs import (
    _check_path_anchors,
    _check_route_anchors,
    _collect_code_routes,
    _load_config,
)


def _scan_all_docs():
    """对全部受检文档跑三类锚点检查，返回 error 档列表。"""
    exclude_docs, exclude_paths, exclude_routes, _, _ = _load_config()
    code_routes = _collect_code_routes()
    errors = []
    for doc in check_docs._DOCS_DIR.glob("*.md"):
        if doc.name in exclude_docs:
            continue
        errors.extend(_check_path_anchors(doc, exclude_paths))
        errors.extend(_check_route_anchors(doc, code_routes, exclude_routes))
    return errors


def test_no_dangling_code_paths_in_docs():
    """docs/agents/*.md 引用的 src 路径必须存在（error 档为空）。"""
    errors = [e for e in _scan_all_docs() if e.kind == "path"]
    assert errors == [], (
        "文档引用了不存在的代码路径（文档已腐化，需更新 docs/agents/）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_doc_routes_registered_in_code():
    """docs 声明的 /api 路由必须在 src/api/ 有 @router 注册（error 档为空）。"""
    errors = [e for e in _scan_all_docs() if e.kind == "route"]
    assert errors == [], (
        "文档声明了代码中不存在的路由（接口已删/改名，需更新文档）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_load_config_defaults():
    """pyproject 排除表应能加载且含核心排除项（意向清单文档不校验）。"""
    exclude_docs, _ep, _er, _es, _est = _load_config()
    assert "requirements_pool.md" in exclude_docs  # 意向清单应被排除
    assert _est == set()  # pyproject 未配置 exclude_skill_tools → 默认空集
    assert check_docs._DOCS_DIR.exists()


def test_skill_tool_anchor_missing_tool(tmp_path):
    """frontmatter allowed-tools 引用代码中不存在工具名 → error 档。"""
    from src.cli import check_docs as cd

    skill_dir = tmp_path / "sample"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sample\ndescription: 示例\nauthorized-tools: []\n"
        "allowed-tools: [retrieve_kb, ghost_tool]\n---\n\n正文",
        encoding="utf-8",
    )

    findings = cd._check_skill_tool_anchors(
        skills_root=tmp_path,
        known_tools={"retrieve_kb", "search_web"},
        exclude=set(),
    )
    errors = [f for f in findings if f.severity == "error"]
    assert any("ghost_tool" in f.anchor for f in errors)
    # retrieve_kb 在 known_tools → 不报
    assert not any(f.anchor == "retrieve_kb" for f in errors)


def test_skill_tool_anchor_exclude_suppresses(tmp_path):
    """allowed-tools 引用的工具名在排除表中 → 不报（示例/伪代码场景）。"""
    from src.cli import check_docs as cd

    skill_dir = tmp_path / "sample"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sample\ndescription: 示例\n"
        "allowed-tools: [ghost_tool]\n---\n\n正文",
        encoding="utf-8",
    )

    findings = cd._check_skill_tool_anchors(
        skills_root=tmp_path,
        known_tools={"retrieve_kb", "search_web"},
        exclude={"ghost_tool"},
    )
    errors = [f for f in findings if f.severity == "error"]
    assert errors == []


def test_skill_body_tool_names_exist_in_code():
    """skill 正文引用的工具名必须存在（Q3 叙述级防腐）。

    skill 正文若引用工具名（如 retrieve_kb、search_web），该工具必须在代码中已注册，
    否则正文会指向不存在的工具。正文属叙述层，不在 check_docs 的 frontmatter 扫描
    范围，故在此用已知工具名词典比对正文（当前 skill 全为 fork，正文应零工具名）。
    """
    import re

    from src.agents.skills.loader import SkillLoader
    from src.cli import check_docs as cd

    _TOOL_PATTERN = re.compile(r"(retrieve_kb|search_web|ask_user|delegate_task)")
    known = cd._collect_known_tool_names()
    skills_root = cd._PROJECT_ROOT / "skills"
    if not skills_root.exists():
        return  # skills 目录未建（Task 9 前）→ 跳过，Task 9 后必有
    for rec in SkillLoader(skills_root).load_all():
        body = (rec.inline_prompt or "") + (rec.fork_body or "")
        for tool_name in set(_TOOL_PATTERN.findall(body)):
            assert tool_name in known, (
                f"skill {rec.name} 正文引用工具 {tool_name} 但代码未注册（已改名/删除？）"
            )


# ── 符号检索的代码快照（change check-docs-symbol-lookup-perf）──


@pytest.fixture(autouse=True)
def _clear_src_blob():
    """每个用例前后清空代码快照。

    快照是**模块级、进程级**的：pytest 会话内模块只 import 一次，不隔离会让
    「替换 _SRC_DIR」的用例污染同会话的其他用例。
    """
    check_docs._reset_cache()
    yield
    check_docs._reset_cache()


def _make_src(tmp_path, monkeypatch, files: dict[str, str]) -> Path:
    """造一个临时 src/ 目录并把它设为检索根，返回该目录。"""
    src = tmp_path / "src"
    src.mkdir()
    for name, content in files.items():
        (src / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(check_docs, "_SRC_DIR", src)
    check_docs._reset_cache()
    return src


def test_symbol_only_in_comment_not_found(tmp_path, monkeypatch):
    """只出现在行内注释里的标识符 → 判为"未检索到"（注释不计入）。"""
    _make_src(
        tmp_path,
        monkeypatch,
        {"mod.py": "value = 1  # ghost_symbol 只在注释里\n"},
    )
    assert check_docs._symbol_exists_in_code("ghost_symbol") is False
    # 同文件的真实标识符仍命中 —— 确认不是整体失配
    assert check_docs._symbol_exists_in_code("value") is True


def test_symbol_spanning_line_boundary_not_found(tmp_path, monkeypatch):
    """跨行不产生假匹配：符号被行边界打断 → 判为"未检索到"。"""
    _make_src(tmp_path, monkeypatch, {"mod.py": "foo\nbar\n"})
    assert check_docs._symbol_exists_in_code("foobar") is False


def test_symbol_lookup_reads_codebase_once(tmp_path, monkeypatch):
    """读取次数不随调用次数增长 —— 规格「读取次数不随符号数增长」的确定性守卫。

    计数方式：替换 `pathlib.Path.read_text`；**不使用耗时断言**（避免把机器性能
    引入测试导致 flaky）。
    """
    src = _make_src(
        tmp_path,
        monkeypatch,
        {f"m{i}.py": f"alpha_{i} = {i}\n" for i in range(3)},
    )

    reads = {"n": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if str(self).startswith(str(src)):
            reads["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    check_docs._symbol_exists_in_code("alpha_0")  # 触发建快照
    after_first = reads["n"]
    assert after_first == 3  # 3 个文件各读一次

    for _ in range(50):
        check_docs._symbol_exists_in_code("alpha_1")
        check_docs._symbol_exists_in_code("no_such_symbol_xyz")

    assert reads["n"] == after_first  # 后续查询不再读盘
