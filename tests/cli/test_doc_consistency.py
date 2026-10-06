"""测试文档防腐检查（src/cli/check_docs.py）— 防受检文档的引用漂移与技术栈漂移。

防腐哲学：文档对代码的"引用必须指向存在物"（单向校验），退役技术名不得出现在
描述**现行机制**的文档里。本测试断言 error 档为空——任何受检文档引用了已删除/
改名的代码路径或路由、或出现已退役技术的字样，pytest 即失败，把"文档过时"变成
可自动发现的信号（配合 pre-commit always_run hook 在提交前拦截）。

受检范围含**对外入口文档**（根 `README.md` / `CLAUDE.md` / `src/api/README.md`，
由 `extra_docs` 声明）—— 这几篇曾长期不在扫描范围内，导致存储栈换了三代而 README
仍写着旧世界（见 `docs/agents/defensive-patterns.md`「开发期闸门」）。
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
    """对全部受检文档跑四类锚点检查，返回 error 档列表。

    取文档口径与 CLI 的 `main()` **完全一致**（都走 `check_docs._iter_doc_paths`）：
    历史上测试自拼 `_DOCS_DIR.glob(...)`、生产另有范围，两套口径让"测试全绿"与
    "根文档无人校验"可以同时成立。
    """
    cfg = _load_config()
    code_routes = _collect_code_routes()
    errors = []
    for doc in check_docs._iter_doc_paths(cfg):
        errors.extend(_check_path_anchors(doc, cfg.exclude_paths))
        errors.extend(_check_route_anchors(doc, code_routes, cfg.exclude_routes))
        errors.extend(check_docs._check_banned_terms(doc, cfg))
    return errors


def test_no_dangling_code_paths_in_docs():
    """受检文档引用的 src 路径必须存在（error 档为空）。"""
    errors = [e for e in _scan_all_docs() if e.kind == "path"]
    assert errors == [], (
        "文档引用了不存在的代码路径（文档已腐化，需更新受影响文档）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_doc_routes_registered_in_code():
    """文档声明的 /api 路由必须在 src/api/ 有 @router 注册（error 档为空）。"""
    errors = [e for e in _scan_all_docs() if e.kind == "route"]
    assert errors == [], (
        "文档声明了代码中不存在的路由（接口已删/改名，需更新文档）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_code_routes_resolve_constant_path(tmp_path, monkeypatch):
    """路由路径以常量声明（`@router.get(CONST)`）时也能解析为路由。

    回归：`src/api/wecom.py` 用从 `src.config.const` 引入的 `WECOM_CALLBACK_PATH`
    声明路径。旧实现只认字符串字面量 → 该路由漏检 → 文档登记后被误报为"路由不存在"。
    """
    src = tmp_path / "src"
    (src / "api").mkdir(parents=True)
    (src / "config").mkdir(parents=True)
    (src / "config" / "const.py").write_text(
        'MY_CALLBACK_PATH: str = "/thing/cb"\n', encoding="utf-8"
    )
    (src / "api" / "m.py").write_text(
        "from src.config.const import MY_CALLBACK_PATH\n\n"
        "router = APIRouter()\n\n\n"
        "@router.get(MY_CALLBACK_PATH)\n"
        "async def h() -> None: ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_docs, "_API_DIR", src / "api")
    monkeypatch.setattr(check_docs, "_SRC_DIR", src)

    assert "/api/thing/cb" in _collect_code_routes()


def test_no_banned_terms_in_checked_docs():
    """受检文档不得出现已退役/改名技术的字样 —— 本次闸门扩展的成果守卫。

    命中说明"技术栈换了但文档没换"：要么改文档，要么（若确属必须保留的溯源）
    加行内标记 `doc-anchors-allow`，要么把该篇登记进 `banned_term_exempt_docs`。
    """
    errors = [e for e in _scan_all_docs() if e.kind == "banned_term"]
    assert errors == [], (
        "受检文档出现已退役/改名技术的字样（改文档，或按机制豁免）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_root_docs_are_in_scan_scope():
    """对外入口文档必须在受检范围内（它们曾长期无人校验）。"""
    cfg = _load_config()
    labels = {check_docs._doc_label(p) for p in check_docs._iter_doc_paths(cfg)}
    assert {"README.md", "CLAUDE.md", "src/api/README.md"} <= labels
    assert not any(lbl.endswith("requirements_pool.md") for lbl in labels)


def test_load_config_defaults():
    """pyproject 排除表应能加载，且默认项都在（并集语义：只能追加）。"""
    cfg = _load_config()
    assert "requirements_pool.md" in cfg.exclude_docs  # 意向清单应被排除
    assert cfg.exclude_skill_tools == set()  # pyproject 配了空表 → 并集仍是默认空集
    assert check_docs._DOCS_DIR.exists()
    # 禁用词与豁免名单来自代码默认值（见 _CONFIG_DEFAULTS）
    assert {"MySQL", "Chroma", "BM25"} <= cfg.banned_terms
    assert "glossary.md" in cfg.banned_term_exempt_docs
    assert {"README.md", "CLAUDE.md"} <= cfg.extra_docs


def _cfg(**overrides):
    """取一份"仅代码默认值"的配置并按需覆盖字段。

    直接由 `_CONFIG_DEFAULTS` 构造而非 `_load_config()`：单测不该受 pyproject
    当前内容影响。
    """
    import dataclasses

    base = check_docs.DocAnchorsConfig(
        **{k: set(v) for k, v in check_docs._CONFIG_DEFAULTS.items()}
    )
    return dataclasses.replace(base, **overrides)


def test_banned_term_flags_hit(tmp_path):
    """禁用词命中 → error 档，带行号与词名。"""
    doc = tmp_path / "README.md"
    doc.write_text("本系统的关系库是 MySQL 8.0。\n", encoding="utf-8")

    findings = check_docs._check_banned_terms(doc, _cfg())

    assert [f.kind for f in findings] == ["banned_term"]
    assert findings[0].severity == "error"
    assert findings[0].anchor == "MySQL"
    assert findings[0].doc_line == 1
    assert findings[0].doc_file == "README.md"


def test_banned_term_is_case_insensitive_substring(tmp_path):
    """大小写不敏感 + 子串匹配：`Chroma` 要同时命中 ChromaDB 与 chromadb。

    报错粒度是「每行 × 每个词」各一条，故同一行里出现两次同词只报一条（避免刷屏）。
    """
    doc = tmp_path / "README.md"
    doc.write_text("第 1 行提到 ChromaDB。\n第 2 行提到 chromadb。\n", encoding="utf-8")

    findings = check_docs._check_banned_terms(doc, _cfg(banned_terms={"Chroma"}))

    assert [f.doc_line for f in findings] == [1, 2]
    assert {f.anchor for f in findings} == {"Chroma"}


def test_banned_term_exempt_doc_not_checked(tmp_path):
    """台账类文档整篇豁免（其职责就是记"曾经用什么"）。"""
    doc = tmp_path / "glossary.md"
    doc.write_text("MySQL：已退役的关系库。\n", encoding="utf-8")

    assert check_docs._check_banned_terms(doc, _cfg()) == []


def test_banned_term_allow_marker_skips_only_that_line(tmp_path):
    """行内标记只豁免该行，不豁免整篇。"""
    doc = tmp_path / "code-map.md"
    doc.write_text(
        "原历史包名 mysql_db 的改名已完成 <!-- doc-anchors-allow -->\n"
        "这一行没有标记，仍应被拦：MySQL 已退役。\n",
        encoding="utf-8",
    )

    findings = check_docs._check_banned_terms(doc, _cfg())

    assert [f.doc_line for f in findings] == [2]


def test_iter_doc_paths_shares_scope_with_cli(tmp_path, monkeypatch):
    """扫描范围只有一处定义：extra_docs 里的新文档必须被 main() 的取文档口径带上。"""
    root = tmp_path
    agents = root / "docs" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.md").write_text("内容\n", encoding="utf-8")
    (root / "README.md").write_text("内容\n", encoding="utf-8")
    monkeypatch.setattr(check_docs, "_DOCS_DIR", agents)
    monkeypatch.setattr(check_docs, "_PROJECT_ROOT", root)

    labels = {
        check_docs._doc_label(p)
        for p in check_docs._iter_doc_paths(
            _cfg(extra_docs={"README.md"}, exclude_docs=set())
        )
    }

    assert labels == {"docs/agents/a.md", "README.md"}


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
    # 首断言只锚定"建快照时 3 个文件各读一次"；**回退探测靠末断言** ——
    # 旧实现若在顺序靠前的文件命中，首查也可能恰好是 3 次（首断言会误过）。
    assert after_first == 3

    for _ in range(50):
        check_docs._symbol_exists_in_code("alpha_1")
        check_docs._symbol_exists_in_code("no_such_symbol_xyz")

    assert reads["n"] == after_first  # 后续查询不再读盘


def test_blob_rebuilds_when_src_root_changes(tmp_path, monkeypatch):
    """根变化时快照自行重建（**不**依赖显式失效函数）—— 根校验分支的护栏。"""
    first = tmp_path / "a" / "src"
    first.mkdir(parents=True)
    (first / "m.py").write_text("alpha_only = 1\n", encoding="utf-8")
    monkeypatch.setattr(check_docs, "_SRC_DIR", first)
    check_docs._reset_cache()
    assert check_docs._symbol_exists_in_code("alpha_only") is True

    second = tmp_path / "b" / "src"
    second.mkdir(parents=True)
    (second / "m.py").write_text("beta_only = 1\n", encoding="utf-8")
    monkeypatch.setattr(check_docs, "_SRC_DIR", second)
    # 故意不调 _reset_cache()：快照必须因根变化而重建
    assert check_docs._symbol_exists_in_code("beta_only") is True
    assert check_docs._symbol_exists_in_code("alpha_only") is False
