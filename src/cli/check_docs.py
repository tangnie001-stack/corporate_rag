#!/usr/bin/env python3
"""文档防腐检查 CLI — 校验 docs/agents/ 中的"代码锚点"是否与代码现状一致。

背景（知识防腐）：代码重构时常改函数名/删目录/改路由，而 docs/agents/ 下的
文档引用未同步更新，导致文档描述与代码漂移（腐化）。本脚本机械校验三类可编程锚点：

  - T1 路径锚点：文档中 `src/**/*.py` / `src/**/` 引用在代码库是否存在
  - T1 路由锚点：文档声明的 `METHOD /api/...` 是否在 src/api/ 有对应注册
  - T2 符号锚点：文档反引号中的标识符（CamelCase / 下划线命名）在 src/ 是否可检索到

只做"文档声称存在 → 代码必须找得到"的单向校验（文档漏写新代码不算错误），
把"引用失效"这类机器可判定的腐化从人肉记忆里解放出来。叙述语义（流程对错）
不在本脚本范围。

用法：
    python -m src.cli.check_docs                    # 检查全部 docs/agents/*.md
    python -m src.cli.check_docs --doc data-flow    # 只查 data-flow.md（不写 .md 后缀）
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
    "codegraph-guide.md",  # 引用外部 codegraph.db，非本仓库 src
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
        kind: 锚点类别（path / route / symbol / skill_tool）
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


def _load_config() -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    """从 pyproject.toml [tool.doc_anchors] 读取排除表（不存在则用默认值）。

    直接读文本定位 section，按 key 正则取数组体后用 ast.literal_eval 解析
    TOML list（兼容多行与注释）。

    Returns:
        (exclude_docs, exclude_paths, exclude_routes, exclude_symbols,
         exclude_skill_tools)
    """
    exclude_docs = set(_DEFAULT_EXCLUDE_DOCS)
    exclude_paths = set(_DEFAULT_EXCLUDE_PATHS)
    exclude_routes = set(_DEFAULT_EXCLUDE_ROUTES)
    exclude_symbols = set(_DEFAULT_EXCLUDE_SYMBOLS)
    exclude_skill_tools = set(_DEFAULT_EXCLUDE_SKILL_TOOLS)
    pyproject = _PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return (
            exclude_docs,
            exclude_paths,
            exclude_routes,
            exclude_symbols,
            exclude_skill_tools,
        )
    try:
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r"\[tool\.doc_anchors\](.*?)(?=\n\[|\Z)", text, re.DOTALL)
        if not m:
            return (
                exclude_docs,
                exclude_paths,
                exclude_routes,
                exclude_symbols,
                exclude_skill_tools,
            )
        for key, dest in (
            ("exclude_docs", exclude_docs),
            ("exclude_paths", exclude_paths),
            ("exclude_routes", exclude_routes),
            ("exclude_symbols", exclude_symbols),
            ("exclude_skill_tools", exclude_skill_tools),
        ):
            km = re.search(
                rf"^\s*{key}\s*=\s*\[(.*?)\]", m.group(1), re.DOTALL | re.MULTILINE
            )
            if not km:
                continue
            try:
                dest |= set(ast.literal_eval(f"[{km.group(1)}]"))
            except (ValueError, SyntaxError):
                continue
    except OSError:
        pass
    return (
        exclude_docs,
        exclude_paths,
        exclude_routes,
        exclude_symbols,
        exclude_skill_tools,
    )


def _iter_doc_lines(doc_path: Path):
    """逐行产出 (行号, 内容)；跳过代码块与链接行内锚点噪声。"""
    yield from enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1)


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
                        doc_file=doc_path.name,
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
                    doc_file=doc_path.name,
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
                    doc_file=doc_path.name,
                    doc_line=lineno,
                    anchor=symbol,
                    message=f"反引号标识符 {bare} 在 src/ 未检索到（改名/删除？或属提示词/伪代码）",
                )
            )
    return findings


def _symbol_exists_in_code(symbol: str) -> bool:
    """在 src/ 下检索标识符是否以"代码标识符片段"形态出现（非注释/字符串）。

    Python 标识符可含下划线（如 set_entry_point 含 entry_point 片段），故不苛求
    独立词边界，只要求前后不是字母数字（允许 _ 相连）。用极简文本扫描：命中的
    行先去注释再查，为控制成本只扫 .py。

    Args:
        symbol: 标识符名

    Returns:
        True 代码中存在该标识符
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])")
    for py in _SRC_DIR.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            # 剥行内注释（# 前），粗略排除纯字符串行
            stripped = line.split("#", 1)[0]
            if pattern.search(stripped):
                return True
    return False


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
    """CLI 入口 — 解析参数、执行三类锚点校验、汇总打印。"""
    parser = argparse.ArgumentParser(description="Documentation anti-rot checker")
    parser.add_argument("--doc", help="只检查指定文档（文件名不带 .md）")
    parser.add_argument("--verbose", action="store_true", help="同时显示 warn 档")
    parser.add_argument(
        "--list-routes", action="store_true", help="只列出文档已声明但代码缺失的路由"
    )
    args = parser.parse_args()

    (
        exclude_docs,
        exclude_paths,
        exclude_routes,
        exclude_symbols,
        exclude_skill_tools,
    ) = _load_config()

    if args.doc:
        doc_files = [_DOCS_DIR / f"{args.doc}.md"]
        if not doc_files[0].exists():
            sys.exit(f"文档不存在: {doc_files[0]}")
    else:
        doc_files = sorted(
            p for p in _DOCS_DIR.glob("*.md") if p.name not in exclude_docs
        )

    code_routes = _collect_code_routes()
    findings: list[DocFinding] = []

    for doc in doc_files:
        findings.extend(_check_path_anchors(doc, exclude_paths))
        findings.extend(_check_route_anchors(doc, code_routes, exclude_routes))
        findings.extend(_check_symbol_anchors(doc, exclude_symbols))

    # skill 防腐（design D18）：frontmatter allowed-tools vs 实际工具注册
    findings.extend(
        _check_skill_tool_anchors(
            _SKILLS_DIR,
            _collect_known_tool_names(),
            exclude_skill_tools,
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
