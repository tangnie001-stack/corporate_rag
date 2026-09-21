"""prompt 模板的启动期校验。

失败语义（spec <prompt-carrier>「模板加载的失败语义」）：
- 启动期校验，任一失败 → **启动失败**（透传 TemplateLoadError）
- **不提供**请求期降级到内嵌正文的分支 —— 否则与"唯一事实源"冲突，
  且故障会在每个请求重复出现

与 Langfuse 官方建议的差异：采纳其"启动期预取"，不采纳 fallback；
该建议的前提是远端源可能不可达，而本项目的模板源是打进镜像的本地文件。

当前校验范围：① 每个 `section` 都至少有一条模板；② 存在 `domain: general` 的
base 模板；③ 全部段模板正文总字符数不超过事故兜底上限。
"""

from __future__ import annotations

from src.config.prompts import loader

# 事故兜底上限：只拦"整篇文档被误粘贴进模板"这类明显损坏，**不是预算闸门**。
# 依据（change design.md D12.8）：未找到厂商的占比建议；同类项目 system prompt
# 实测 6K–24K 字符且普遍无硬上限（codex 6,621–24,026；WeKnora 单模板 18,120）。
SECTION_CHARS_LIMIT: int = 50000


def _count_section_chars(templates: dict[str, loader.Template]) -> dict[str, int]:
    """统计每个段全部模板正文的字符数之和。

    Args:
        templates: 模板 id → 模板（loader.load_all 的返回值）

    Returns:
        段名 → 字符数之和；键序为首次出现顺序，稳定
    """
    section_chars: dict[str, int] = {}
    for template in templates.values():
        if template.kind != "section":
            continue
        assert template.section is not None  # loader 已校验，此处仅供类型收窄
        section_chars[template.section] = section_chars.get(template.section, 0) + len(
            template.content
        )
    return section_chars


def section_char_totals() -> dict[str, int]:
    """返回各段总字符数（**不做校验**，供请求期观测借用）。

    模板是打进镜像的静态文件，启动期已由 `validate_all()` 校验且运行期不变，
    故请求期只借用计数结果，不重复校验、也不会因模板问题在请求期失败。

    Returns:
        段名 → 该段全部模板正文的字符数之和（键序为本期加载顺序，即**文件名顺序**，
        非组装顺序；组装顺序待引入组装时对齐）
    """
    return _count_section_chars(loader.load_all())


def validate_all() -> dict[str, int]:
    """校验全部模板并返回各段总字符数（启动期调用，失败即进程启动失败）。

    Returns:
        段名 → 该段全部模板正文的字符数之和（键序为首次出现顺序，稳定）

    Raises:
        loader.TemplateLoadError: 目录/YAML/id 非法，或某段无模板、
            缺少 domain=general 的 base 模板、总字符数超出事故兜底上限
    """
    templates = loader.load_all()

    section_chars = _count_section_chars(templates)

    missing_sections = [
        name for name in sorted(loader.VALID_SECTIONS) if name not in section_chars
    ]
    if missing_sections:
        raise loader.TemplateLoadError(f"以下 section 没有任何模板：{missing_sections}")

    has_general = any(
        t.kind == "section" and t.section == "base" and t.domain == "general"
        for t in templates.values()
    )
    if not has_general:
        raise loader.TemplateLoadError(
            "缺少 domain=general 的 base 模板（默认值会自身非法）"
        )

    total = sum(section_chars.values())
    if total > SECTION_CHARS_LIMIT:
        raise loader.TemplateLoadError(
            f"段模板总字符数 {total} 超过事故兜底上限 {SECTION_CHARS_LIMIT}；"
            f"各段：{section_chars}"
        )
    return section_chars
