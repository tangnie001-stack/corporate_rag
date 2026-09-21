"""prompt 模板加载入口 —— YAML → 模板映射 → 段查询 → 文本渲染。

本模块是模板的唯一读取点（spec <prompt-carrier>「加载入口与占位符规则」「读取路径唯一化」）。

职责边界：
- 只负责"读模板 + 渲染占位符"；**不负责**段组装顺序与条件注入（那在 rag/prompt.py，P1 处理）
- 占位符只做**仅标识符式**替换（{identifier}），未提供的原样保留，不求值表达式
  （沿用既有否决：不引入 Jinja2，见 docs/tmp/deep-research-prompt-management.md 第四部分）
- 模板目录固定为 <包目录>/templates；不做远端数据源（终态预留，本期不实现）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"

VALID_SECTIONS: frozenset[str] = frozenset(
    {"base", "runtime_contract", "sources", "tools", "output"}
)

# 仅标识符式占位符：{name}，允许 ASCII 字母/数字/下划线
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class Template:
    """一个 prompt 模板。

    Attributes:
        id: 模板唯一标识（kebab-case）；来源：YAML 的 id 字段；用途：按 id 取值
        kind: 段模板或任务模板（section|task）；来源：YAML；用途：决定是否参与段组装
        section: 段名（仅 kind=section）；来源：YAML；用途：段归属
        domain: 领域名（仅 section=base）；来源：YAML；用途：base 三选一（P1）
        content: 正文（逐字）；来源：YAML 的 content 块标量；用途：最终拼进 prompt
    """

    id: str
    kind: str
    content: str
    section: str | None = None
    domain: str | None = None


class TemplateLoadError(RuntimeError):
    """模板加载/校验失败（启动期透传，不留请求期降级分支）。"""


def _read_file(path: Path) -> list[Template]:
    """读取单个模板文件，返回其中的模板列表。

    Args:
        path: 模板文件路径

    Returns:
        该文件内的模板列表

    Raises:
        TemplateLoadError: YAML 无法解析，或根节点/条目结构非法
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateLoadError(f"模板文件不可读：{path}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TemplateLoadError(f"模板 YAML 解析失败：{path}") from exc
    if not isinstance(data, dict) or "templates" not in data:
        raise TemplateLoadError(f"模板文件缺少 templates 根键：{path}")
    items = data["templates"]
    if not isinstance(items, list):
        raise TemplateLoadError(f"templates 不是列表：{path}")
    result: list[Template] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise TemplateLoadError(f"模板条目不是映射：{path}")
        tid = entry.get("id")
        kind = entry.get("kind")
        content = entry.get("content")
        if not isinstance(tid, str) or not tid:
            raise TemplateLoadError(f"模板缺少合法的 id：{path}")
        if kind not in {"section", "task"}:
            raise TemplateLoadError(f"模板 {tid} 的 kind 非法：{kind!r}")
        if not isinstance(content, str):
            raise TemplateLoadError(f"模板 {tid} 缺少正文")
        section = entry.get("section")
        domain = entry.get("domain")
        if kind == "task" and section is not None:
            raise TemplateLoadError(f"task 模板 {tid} 不得带 section：{section!r}")
        if kind == "section" and section not in VALID_SECTIONS:
            raise TemplateLoadError(f"模板 {tid} 的 section 非法：{section!r}")
        if section == "base" and not domain:
            raise TemplateLoadError(f"base 模板 {tid} 缺少 domain")
        result.append(
            Template(id=tid, kind=kind, content=content, section=section, domain=domain)
        )
    return result


@lru_cache(maxsize=1)
def load_all() -> dict[str, Template]:
    """加载全部模板（进程内缓存）。

    Returns:
        以模板 id 为键的映射

    Raises:
        TemplateLoadError: 目录不存在、YAML 非法、id 重复
    """
    if not TEMPLATES_DIR.is_dir():
        raise TemplateLoadError(f"模板目录不存在：{TEMPLATES_DIR}")
    mapping: dict[str, Template] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        for template in _read_file(path):
            if template.id in mapping:
                raise TemplateLoadError(f"模板 id 重复：{template.id}")
            mapping[template.id] = template
    if not mapping:
        raise TemplateLoadError(f"模板目录为空：{TEMPLATES_DIR}")
    return mapping


def get_content(template_id: str) -> str:
    """按 id 取模板正文。

    Args:
        template_id: 模板 id

    Returns:
        模板正文（逐字，不做任何处理）

    Raises:
        KeyError: 该 id 不存在
    """
    return load_all()[template_id].content


def get_by_section(section: str) -> list[Template]:
    """按段取全部段模板（不含 kind=task）。

    Args:
        section: 段名（base/runtime_contract/sources/tools/output）

    Returns:
        该段的模板列表（顺序为文件名升序、文件内声明顺序，稳定）
    """
    return [
        t for t in load_all().values() if t.kind == "section" and t.section == section
    ]


def get_domain_base(domain: str) -> str:
    """按领域取 base 正文。

    Args:
        domain: 领域名（如 finance；通用用保留值 general）

    Returns:
        该领域的 base 正文

    Raises:
        KeyError: 无匹配模板
    """
    for template in get_by_section("base"):
        if template.domain == domain:
            return template.content
    raise KeyError(domain)


def has_domain(domain: str) -> bool:
    """是否存在该领域的 base 模板（领域识别判据，不抛异常）。

    服务层用它做**写入前**校验（spec <prompt-composition>「知识库领域绑定」：
    领域标识不满足该判据的值 SHALL 在写入前被拒绝，而非读取时静默回退）。

    Args:
        domain: 领域名

    Returns:
        True = 存在 kind=section 且 section=base 且 domain 等于该值的模板
    """
    for template in get_by_section("base"):
        if template.domain == domain:
            return True
    return False


def render(text: str, variables: dict[str, str]) -> str:
    """仅标识符式替换占位符；未提供的占位符原样保留。

    Args:
        text: 含占位符的模板正文
        variables: 变量名 → 值

    Returns:
        替换后的文本（未提供的占位符保持 {name} 原样；{{name}} 这类表达式写法
        整体原样输出，不做部分替换）
    """

    def _substitute(match: re.Match[str]) -> str:
        """单次匹配的替换回调：未提供或属于双花括号写法则原样返回。

        Args:
            match: 占位符正则的一次匹配（``match.string`` 为原始文本）

        Returns:
            替换值；未提供的占位符、以及紧邻花括号的 ``{{name}}`` 写法均原样返回
        """
        source = match.string
        start = match.start()
        end = match.end()
        if start > 0 and source[start - 1] == "{":
            return match.group(0)
        if end < len(source) and source[end] == "}":
            return match.group(0)
        name = match.group(1)
        if name in variables:
            return variables[name]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_substitute, text)
