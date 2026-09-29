#!/usr/bin/env python3
"""文档防腐检查 CLI — 校验 docs/agents/ 中的"代码锚点"是否与代码现状一致。

背景（知识防腐）：代码重构时常改函数名/删目录/改路由，而文档引用未同步更新，
导致文档描述与代码漂移（腐化）。本脚本机械校验三类可编程锚点 + 一类"禁用词"：

  - T1 路径锚点：文档中 `src/**/*.py` / `src/**/` 引用在代码库是否存在
  - T1 路由锚点：文档声明的 `METHOD /api/...` 是否在 src/api/ 有对应注册
  - T2 符号锚点：文档反引号中的标识符（CamelCase / 下划线命名）在 src/ 是否可检索到
  - T3 禁用词锚点：受检文档是否出现已退役/改名技术的字样（error 档）

前三类只做"文档声称存在 → 代码必须找得到"的单向校验（文档漏写新代码不算错误），
把"引用失效"这类机器可判定的腐化从人肉记忆里解放出来；T3 补的是另一类漂移 ——
"技术栈换了但文档没换"（如关系库换成 PostgreSQL 后文档仍写 MySQL），它没有可
grep 的代码符号，只能靠词表拦。叙述语义（流程对错）不在本脚本范围。

扫描范围（**对外入口文档也在内**，它们的漂移同样致命）：
  - `docs/agents/*.md`（非递归，减 exclude_docs）
  - `extra_docs` 列出的项目内文档（根 README.md / CLAUDE.md、子系统 README 等）
  - `skills/*/SKILL.md` 的 frontmatter allowed-tools

用法：
    python -m src.cli.check_docs                    # 检查全部受检文档
    python -m src.cli.check_docs --doc data-flow    # 只查一篇（docs/agents 名或项目内相对路径）
    python -m src.cli.check_docs --verbose          # 显示 warn 档
    python -m src.cli.check_docs --list-routes      # 列出文档已声明但代码缺失的路由

退出码：0 = 无 error 档；1 = 存在 error 档（供 pre-commit / CI 判失败）。
warn 档仅提示需人工 triage，不影响退出码。

排除规则（pyproject.toml [tool.doc_anchors]）：
  exclude_docs: 整篇跳过校验的文档（如 requirements_pool.md 为意向清单）
  exclude_paths: 文档中允许指向不存在代码的路径前缀
  exclude_routes: 文档中允许声明但代码无注册的路由
  exclude_symbols: 允许出现在文档但无需在代码中存在的标识符
  exclude_skill_tools: skill frontmatter allowed-tools 中允许引用但代码不存在的工具名
  extra_docs: 除 docs/agents/*.md 外还要校验的项目内文档（相对项目根）
  banned_terms: T3 禁用词表（大小写不敏感子串），命中即 error
  banned_term_exempt_docs: 整篇豁免 T3 的文档（台账/术语/事故档案类允许叙述退役史）
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = _PROJECT_ROOT / "docs" / "agents"
_SRC_DIR = _PROJECT_ROOT / "src"
_API_DIR = _SRC_DIR / "api"
_SKILLS_DIR = _PROJECT_ROOT / "skills"

# ── 默认排除表（pyproject.toml 可覆盖/扩展）──
# requirements_pool.md 是"意向清单"，常写未来模块；reference-projects.md 指外部仓库
_DEFAULT_EXCLUDE_DOCS = {
    "requirements_pool.md",
    "reference-projects.md",
}
# 文档中允许指向"不存在代码"的路径前缀（如规划中的目录）
_DEFAULT_EXCLUDE_PATHS: set[str] = set()
# 允许文档声明但代码暂无注册的路由（如已规划端点）
_DEFAULT_EXCLUDE_ROUTES: set[str] = set()
# 允许出现在文档的英文标识符（避免启发式误报；补充全小写词/通用词）
_DEFAULT_EXCLUDE_SYMBOLS = {
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "src",
    "ok",
    "auth",
    "chat",
    "rag",
    "kb",
    "kb_id",
    "doc_id",
}
# skill allowed-tools 中允许引用但代码不存在的工具名（示例/伪代码）
_DEFAULT_EXCLUDE_SKILL_TOOLS: set[str] = set()
# 除 docs/agents/*.md 外还要校验的项目内文档（相对项目根）。这些是对外入口文档，
# 其漂移与 docs/agents 同样致命，但历史上一直不在扫描范围内（见本模块 docstring）。
_DEFAULT_EXTRA_DOCS = {
    "README.md",
    "CLAUDE.md",
    "src/api/README.md",
}
# T3 禁用词：已退役/改名技术的字样，出现在受检文档即 error。用子串匹配（大小写
# 不敏感），故 "Chroma" 覆盖 ChromaDB/chromadb、"BM25" 覆盖 rank_bm25/bm25_score。
# 不列 MCP / LiteLLM / qwen3.7-* 等 —— 它们属现状或"计划中"，需合法出现。
_DEFAULT_BANNED_TERMS = {
    "MySQL",
    "Chroma",
    "BM25",
    "financial-qa",
    "create_react_agent",
    "sse_utils",
    "qwen-max",
    "text-embedding-v3",
    "gte-rerank",
}
# 整篇豁免 T3 的文档：其职责就是记录"曾经用什么"（术语表 / 接口契约的历史踩坑表 /
# 操作记录 / 缺陷档案），要求它们不出现退役技术名等于删掉溯源。
_DEFAULT_BANNED_TERM_EXEMPT_DOCS = {
    "glossary.md",
    "api_contract.md",
    "cookbook.md",
    "defensive-patterns.md",
}
# 行内逃生标记：行内含该串则跳过该行的 T3 检查。用于"现行机制文档里偶有一处
# 历史注记"（如 code-map 里记"原历史包名 mysql_db 的改名已完成"）这种无法回避、
# 也不该删的溯源，避免为一行说明把整篇文档移出保护范围。
_BANNED_TERM_ALLOW_MARKER = "doc-anchors-allow"

# ── 正则锚点提取 ──
_PATH_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_])"
    r"(src/[A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)*/?)"
    r"(?=[^A-Za-z0-9_./-]|$)"
)
# 只匹配 src/ 开头的完整路径（文件或目录），不含行号后缀
_ROUTE_RE = re.compile(r"(GET|POST|PUT|DELETE|PATCH) (/api/[A-Za-z0-9_/{}\-]+)")
# 反引号内 CamelCase / UPPER_SNAKE / snake_case 标识符（排除明显英文句词）
_SYMBOL_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)`")
_SYMBOL_SHAPE_RE = re.compile(
    r"(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)"  # CamelCase
    r"|(?:[A-Z][A-Z0-9_]+)"  # UPPER_SNAKE
    r"|(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+)"  # snake_case（至少一个下划线）
)


@dataclass
class DocFinding:
    """一次锚点校验结果。

    Attributes:
        severity: 严重级别（error=引用失效需修文档；warn=启发式需人 triage）
        kind: 锚点类别（path / route / symbol / skill_tool / banned_term）
        doc_file: 来源文档文件名
        doc_line: 锚点在文档中的行号
        anchor: 文档中出现的锚点原文
        message: 人类可读的说明
    """

    severity: str
    kind: str
    doc_file: str
    doc_line: int
    anchor: str
    message: str


@dataclass
class DocAnchorsConfig:
    """`[tool.doc_anchors]` 的解析结果（默认表 ∪ pyproject 表）。

    各键语义见模块 docstring「排除规则」。集合类一律取**并集**（pyproject 只能追加，
    不能把默认项摘掉）—— 默认项是保护网，不提供"静默削弱它"的路径。

    Attributes:
        exclude_docs: 整篇跳过全部锚点校验的文档名
        exclude_paths: 允许指向不存在代码的路径前缀
        exclude_routes: 允许声明但代码无注册的路由
        exclude_symbols: 允许出现在文档但无需在代码中存在的标识符
        exclude_skill_tools: 允许 skill frontmatter 引用但代码未注册的工具名
        extra_docs: 除 docs/agents/*.md 外还要校验的项目内文档（相对项目根）
        banned_terms: T3 禁用词表
        banned_term_exempt_docs: 整篇豁免 T3 的文档名
    """

    exclude_docs: set[str]
    exclude_paths: set[str]
    exclude_routes: set[str]
    exclude_symbols: set[str]
    exclude_skill_tools: set[str]
    extra_docs: set[str]
    banned_terms: set[str]
    banned_term_exempt_docs: set[str]


# 配置键 → DocAnchorsConfig 字段名（两者同名，列出以固定解析顺序与默认值来源）
_CONFIG_DEFAULTS: dict[str, set[str]] = {
    "exclude_docs": _DEFAULT_EXCLUDE_DOCS,
    "exclude_paths": _DEFAULT_EXCLUDE_PATHS,
    "exclude_routes": _DEFAULT_EXCLUDE_ROUTES,
    "exclude_symbols": _DEFAULT_EXCLUDE_SYMBOLS,
    "exclude_skill_tools": _DEFAULT_EXCLUDE_SKILL_TOOLS,
    "extra_docs": _DEFAULT_EXTRA_DOCS,
    "banned_terms": _DEFAULT_BANNED_TERMS,
    "banned_term_exempt_docs": _DEFAULT_BANNED_TERM_EXEMPT_DOCS,
}


def _load_config() -> DocAnchorsConfig:
    """从 pyproject.toml [tool.doc_anchors] 读取排除表（不存在则用默认值）。

    直接读文本定位 section，按 key 正则取数组体后用 ast.literal_eval 解析
    TOML list（兼容多行与注释）。

    Returns:
        DocAnchorsConfig：每个键都是「默认表 ∪ pyproject 表」
    """
    values = {key: set(default) for key, default in _CONFIG_DEFAULTS.items()}
    pyproject = _PROJECT_ROOT / "pyproject.toml"
    section = ""
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        m = re.search(r"\[tool\.doc_anchors\](.*?)(?=\n\[|\Z)", text, re.DOTALL)
        if m:
            section = m.group(1)
    for key, dest in values.items():
        km = re.search(rf"^\s*{key}\s*=\s*\[(.*?)\]", section, re.DOTALL | re.MULTILINE)
        if not km:
            continue
        try:
            dest |= set(ast.literal_eval(f"[{km.group(1)}]"))
        except (ValueError, SyntaxError):
            continue
    return DocAnchorsConfig(**values)


def _iter_doc_lines(doc_path: Path):
    """逐行产出 (行号, 内容)；跳过代码块与链接行内锚点噪声。"""
    yield from enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1)


def _doc_label(doc_path: Path) -> str:
    """报告里用的文档标识：项目内相对路径，取不到时退回文件名。

    必须带路径而非裸文件名 —— 扫描范围含多个 README.md（根 / `src/api/`），
    只用 `name` 会让报告无法区分是哪一篇。

    Args:
        doc_path: 文档路径

    Returns:
        相对项目根的路径字符串（如 `docs/agents/data-flow.md`）
    """
    try:
        return str(doc_path.relative_to(_PROJECT_ROOT))
    except ValueError:
        return doc_path.name


def _iter_doc_paths(cfg: DocAnchorsConfig) -> list[Path]:
    """产出全部受检文档：`docs/agents/*.md`（减 exclude_docs）+ 存在的 `extra_docs`。

    **这是扫描范围的唯一来源** —— CLI 与测试都经它取文档，避免两处各写一套范围
    （历史上正是这种分叉让根 README / CLAUDE.md 长期无人校验）。

    Args:
        cfg: 配置（排除表 + 扫描范围）

    Returns:
        去重后的文档路径列表（按字符串排序，稳定输出）
    """
    paths = [
        p for p in sorted(_DOCS_DIR.glob("*.md")) if p.name not in cfg.exclude_docs
    ]
    paths += [
        p
        for p in (_PROJECT_ROOT / rel for rel in sorted(cfg.extra_docs))
        if p.exists() and p.name not in cfg.exclude_docs
    ]
    return paths


def _check_path_anchors(doc_path: Path, exclude_paths: set[str]) -> list[DocFinding]:
    """校验 T1 路径锚点：文档中 src/** 引用在代码库是否存在。

    Args:
        doc_path: 待校验文档
        exclude_paths: 允许指向不存在代码的路径前缀

    Returns:
        失效路径的 error 档列表
    """
    findings: list[DocFinding] = []
    for lineno, line in _iter_doc_lines(doc_path):
        for match in _PATH_RE.finditer(line):
            raw = match.group(1)
            ref = raw.rstrip("/")
            # 排除配置中声明的允许缺失前缀
            if any(ref.startswith(p) or p.startswith(ref) for p in exclude_paths):
                continue
            # ref 形如 "src/api/chat.py"，相对项目根校验（src 在 _SRC_DIR）
            rel = ref.removeprefix("src/")
            candidate = _SRC_DIR / rel
            if not candidate.exists():
                findings.append(
                    DocFinding(
                        severity="error",
                        kind="path",
                        doc_file=_doc_label(doc_path),
                        doc_line=lineno,
                        anchor=ref,
                        message=f"文档引用 {ref} 在代码库不存在（文件/目录已删除或改名？）",
                    )
                )
    return findings


def _collect_code_routes() -> set[str]:
    """AST 扫描 src/api/*.py 路由装饰器，返回完整路径集合（含 /api 前缀）。

    Returns:
        代码中注册的路由路径集合，如 {"/api/chat/stream", ...}
    """
    routes: set[str] = set()
    prefix = "/api"  # main.py 对所有 router 统一 include_router(prefix="/api")
    for py in _API_DIR.rglob("*.py"):
        if "__" in py.name or py.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not (
                    isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Attribute)
                    and isinstance(deco.func.value, ast.Name)
                    and deco.func.value.id == "router"
                ):
                    continue
                if not deco.args:
                    continue
                path_arg = deco.args[0]
                if isinstance(path_arg, ast.Constant) and isinstance(
                    path_arg.value, str
                ):
                    routes.add(prefix + path_arg.value)
    return routes


def _check_route_anchors(
    doc_path: Path, code_routes: set[str], exclude_routes: set[str]
) -> list[DocFinding]:
    """校验 T1 路由锚点：文档声明的 METHOD /api/... 在代码是否有对应注册。

    Args:
        doc_path: 待校验文档
        code_routes: 代码中已注册的路由集合
        exclude_routes: 允许文档声明但代码无注册的路由

    Returns:
        error 档（文档有、代码无）
    """
    findings: list[DocFinding] = []
    for lineno, line in _iter_doc_lines(doc_path):
        for _method, path in _ROUTE_RE.findall(line):
            if path in exclude_routes or path in code_routes:
                continue
            findings.append(
                DocFinding(
                    severity="error",
                    kind="route",
                    doc_file=_doc_label(doc_path),
                    doc_line=lineno,
                    anchor=path,
                    message=f"文档声明路由 {path} 但 src/api/ 无对应 @router 注册（已删除/改名？）",
                )
            )
    return findings


def _check_symbol_anchors(
    doc_path: Path, exclude_symbols: set[str]
) -> list[DocFinding]:
    """校验 T2 符号锚点：文档反引号内标识符在 src/ 是否可 grep 到。

    启发式：只查代码库内"应定义类/函数/常量"的命名形态（CamelCase / UPPER_SNAKE /
    带下划线 snake），全小写普通英文词不查。结果标 warn 需人工 triage。

    Args:
        doc_path: 待校验文档
        exclude_symbols: 允许出现在文档的标识符白名单

    Returns:
        未在代码中找到符号的 warn 档列表
    """
    findings: list[DocFinding] = []
    for lineno, line in _iter_doc_lines(doc_path):
        for match in _SYMBOL_RE.finditer(line):
            symbol = match.group(1)
            # 只查代码库内"应定义"的命名形态；去掉可能带点号访问链的尾段
            bare = symbol.split(".")[-1]
            if not _SYMBOL_SHAPE_RE.fullmatch(bare):
                continue
            if bare in exclude_symbols:
                continue
            # grep src/：符号作为标识符出现（含定义与引用）。行内符号只取最长形态
            if _symbol_exists_in_code(bare):
                continue
            findings.append(
                DocFinding(
                    severity="warn",
                    kind="symbol",
                    doc_file=_doc_label(doc_path),
                    doc_line=lineno,
                    anchor=symbol,
                    message=f"反引号标识符 {bare} 在 src/ 未检索到（改名/删除？或属提示词/伪代码）",
                )
            )
    return findings


def _check_banned_terms(doc_path: Path, cfg: DocAnchorsConfig) -> list[DocFinding]:
    """校验 T3 禁用词：文档是否出现已退役/改名技术的字样。

    补的是"技术栈换了但文档没换"这类漂移 —— 它没有可 grep 的代码符号（`MySQL`
    在 Python 里当然"不存在"，但那是必然的，不构成信号），只能靠词表拦。根文档与
    描述**现行机制**的文档不得出现退役技术名；整篇豁免名单（术语表 / 接口契约的
    历史踩坑表 / 操作记录 / 缺陷档案）与行内标记是两条逃生口，见模块 docstring。

    Args:
        doc_path: 待校验文档
        cfg: 配置（禁用词表 + 整篇豁免名单）

    Returns:
        命中禁用词的 error 档列表
    """
    findings: list[DocFinding] = []
    if doc_path.name in cfg.banned_term_exempt_docs:
        return findings
    # 先按长度降序，命中更具体的词优先（如 BM25 优先于 BM25_score 的说明顺序）；
    # 同长度按字母序，保证输出稳定。
    terms = sorted(cfg.banned_terms, key=lambda t: (-len(t), t))
    for lineno, line in _iter_doc_lines(doc_path):
        if _BANNED_TERM_ALLOW_MARKER in line:
            continue
        lowered = line.lower()
        for term in terms:
            if term.lower() not in lowered:
                continue
            findings.append(
                DocFinding(
                    severity="error",
                    kind="banned_term",
                    doc_file=_doc_label(doc_path),
                    doc_line=lineno,
                    anchor=term,
                    message=(
                        f"出现已退役/改名技术的字样 {term}（现行机制文档不得描述旧技术；"
                        f"确属溯源的注记可加行内标记 {_BANNED_TERM_ALLOW_MARKER}）"
                    ),
                )
            )
    return findings


# ── 代码快照（进程级）──
# 快照 = `src/**/*.py` 各行剥行内注释后拼接；进程级、无 TTL、不落盘。
# 等价性论证与边界取舍见 change `check-docs-symbol-lookup-perf` 的 design.md（D2 / D3 / D6）。
_SRC_BLOB: str | None = None
_SRC_BLOB_ROOT: Path | None = None


def _src_blob() -> str:
    """返回 `src/` 下 `.py` 剥行内注释后的文本快照（进程级，首次调用时构建）。

    内容：每行取行内注释前部分（`line.split("#", 1)[0]`），以换行符拼接；
    `_SRC_DIR` 被替换时重建。

    非线程安全：调用方为单线程 CLI（pre-commit 钩子与测试），`_SRC_DIR` 在单次
    运行内不被改写。

    Returns:
        快照字符串。

    Raises:
        UnicodeDecodeError: 任一 `.py` 非 UTF-8 时透传（有意不捕获，见 design.md D6）。
            因快照必读全树，其触发面比"命中即短路"的逐文件读取更广：存在坏文件时
            **每次**符号查询都会抛出。
    """
    global _SRC_BLOB, _SRC_BLOB_ROOT
    if _SRC_BLOB is None or _SRC_BLOB_ROOT != _SRC_DIR:
        parts: list[str] = []
        for py in sorted(_SRC_DIR.rglob("*.py")):
            try:
                text = py.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                parts.append(line.split("#", 1)[0])
        _SRC_BLOB = "\n".join(parts)
        _SRC_BLOB_ROOT = _SRC_DIR
    return _SRC_BLOB


def _reset_cache() -> None:
    """清空本进程的代码快照（测试隔离用；长驻进程亦可显式调用来失效）。"""
    global _SRC_BLOB, _SRC_BLOB_ROOT
    _SRC_BLOB = None
    _SRC_BLOB_ROOT = None


def _symbol_exists_in_code(symbol: str) -> bool:
    """在 `src/` 快照中检索标识符是否以"代码标识符片段"形态出现（非注释）。

    Python 标识符可含下划线（如 set_entry_point 含 entry_point 片段），故不苛求
    独立词边界，只要求前后不是字母数字（允许 _ 相连）。判定在 `_src_blob()` 的
    快照上以一次正则扫描完成。

    Args:
        symbol: 标识符名

    Returns:
        True 代码中存在该标识符
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])")
    return pattern.search(_src_blob()) is not None


def _collect_known_tool_names() -> set[str]:
    """收集代码中实际注册的工具名（registry.register / @tool("...") 字面量）。

    极简 AST/文本扫描 src/agents/tools/ 与 src/agents/skills/：匹配
    registry.register("<name>", ...) 与 @tool("<name>", ...) 字面量。

    Returns:
        实际工具名集合，如 {"retrieve_kb", "ask_user", "search_web", "delegate_task"}
    """
    names: set[str] = set()
    roots = (
        _PROJECT_ROOT / "src" / "agents" / "tools",
        _PROJECT_ROOT / "src" / "agents" / "skills",
    )
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__" in py.name:
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except OSError:
                continue
            # register("name", / @tool("name") 字面量
            for m in re.finditer(r'(?:register|tool)\(\s*"([^"]+)"', text):
                names.add(m.group(1))
    return names


def _parse_skill_frontmatter_tools(path: Path) -> list[tuple[int, str]]:
    """从 SKILL.md 提取 allowed-tools 引用（行号 + 工具名）。

    Args:
        path: SKILL.md 文件路径

    Returns:
        [(行号, 工具名)] 列表
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    inside = False
    current: list[str] = []
    start_line = 0
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "---":
            if not inside:
                inside = True
                current = []
                start_line = idx
                continue
            # 闭合：解析收集到的 frontmatter 行
            body = "\n".join(current)
            tools: list[tuple[int, str]] = []
            for m in re.finditer(r"allowed-tools\s*:\s*\[([^\]]*)\]", body):
                # YAML 允许 [a, b] 与 ["a", "b"] 两种写法，逐项剥引号/空白
                for raw in m.group(1).split(","):
                    tool = raw.strip().strip("\"'")
                    if tool:
                        tools.append((start_line, tool))
            return tools
        if inside:
            current.append(line)
    return []


def _check_skill_tool_anchors(
    skills_root: Path, known_tools: set[str], exclude: set[str]
) -> list[DocFinding]:
    """校验 skill frontmatter allowed-tools 引用的工具名是否在代码实际注册。

    design D18 防腐扩展：工具改名后 skill 若仍引用旧名会静默失效，本函数机械
    检出。只做单向存在性校验（skill 声称存在 → 代码必须找得到）。

    Args:
        skills_root: skills 内容库根目录
        known_tools: 代码中实际注册的工具名集合
        exclude: 允许引用但代码不存在的工具名（示例/伪代码，pyproject 排除表）

    Returns:
        error 档列表（引用代码不存在工具名的 skill frontmatter）
    """
    findings: list[DocFinding] = []
    if not skills_root.exists():
        return findings
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        path = skill_dir / "SKILL.md"
        if not path.exists():
            continue
        for lineno, tool_name in _parse_skill_frontmatter_tools(path):
            if tool_name in exclude or tool_name in known_tools:
                continue
            findings.append(
                DocFinding(
                    severity="error",
                    kind="skill_tool",
                    doc_file=f"skills/{skill_dir.name}/SKILL.md",
                    doc_line=lineno,
                    anchor=tool_name,
                    message=f"skill allowed-tools 引用工具 {tool_name} 但代码未注册（已改名/删除？）",
                )
            )
    return findings


def main() -> None:
    """CLI 入口 — 解析参数、执行三类锚点 + 禁用词校验、汇总打印。"""
    parser = argparse.ArgumentParser(description="Documentation anti-rot checker")
    parser.add_argument(
        "--doc",
        help="只检查指定文档（docs/agents 下的名字，或项目内相对路径，均不带 .md）",
    )
    parser.add_argument("--verbose", action="store_true", help="同时显示 warn 档")
    parser.add_argument(
        "--list-routes", action="store_true", help="只列出文档已声明但代码缺失的路由"
    )
    args = parser.parse_args()

    cfg = _load_config()

    if args.doc:
        # 先按 docs/agents 下的名字找，再退回项目内相对路径（根 README / src/api/README 等）
        candidate = _DOCS_DIR / f"{args.doc}.md"
        if not candidate.exists():
            candidate = _PROJECT_ROOT / f"{args.doc}.md"
        if not candidate.exists():
            sys.exit(f"文档不存在: {args.doc}")
        doc_files = [candidate]
    else:
        doc_files = _iter_doc_paths(cfg)

    code_routes = _collect_code_routes()
    findings: list[DocFinding] = []

    for doc in doc_files:
        findings.extend(_check_path_anchors(doc, cfg.exclude_paths))
        findings.extend(_check_route_anchors(doc, code_routes, cfg.exclude_routes))
        findings.extend(_check_symbol_anchors(doc, cfg.exclude_symbols))
        findings.extend(_check_banned_terms(doc, cfg))

    # skill 防腐（design D18）：frontmatter allowed-tools vs 实际工具注册
    findings.extend(
        _check_skill_tool_anchors(
            _SKILLS_DIR,
            _collect_known_tool_names(),
            cfg.exclude_skill_tools,
        )
    )

    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]

    if args.list_routes:
        for f in errors:
            if f.kind == "route":
                print(f.anchor)
        return

    for f in errors:
        print(f"[error] {f.doc_file}:{f.doc_line} ({f.kind}) {f.message}")
    if args.verbose:
        for f in warns:
            print(f"[warn ] {f.doc_file}:{f.doc_line} ({f.kind}) {f.message}")
    print(f"\n{doc_files.__len__()} 篇文档，{len(errors)} error, {len(warns)} warn")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
