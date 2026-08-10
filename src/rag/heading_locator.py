"""标题段区间定位器 — 由标题树 + 全文建立标题段区间，反推 chunk 归属。"""

import re


def build_heading_segments(
    full_text: str, heading_tree: list[tuple[int, str]]
) -> list[dict]:
    """由标题树和全文建立标题段区间表。

    标题定位以 heading_tree 为准（而不是扫描全文 # 行）：
    docx 的标题是纯文本段落（无 # 前缀），PDF 的标题行带 # 前缀，
    两种形态都先按标题文本 find 定位，再退化到逐行匹配。

    Args:
        full_text: 文档全文（PDF 为 Markdown，docx 为纯文本段落拼接）
        heading_tree: (level, heading) 列表，来自 ParseResult.heading_tree

    Returns:
        标题段区间列表，每项 {"path", "start", "end"}；
        path 为父级拼接的标题路径，start/end 为全文中的字符偏移。
        无标题树或全部标题均未命中时返回空列表。
    """
    if not heading_tree:
        return []

    # 逐标题定位行首偏移（heading_tree 本身按文档顺序排列）
    offsets = []
    for level, title in heading_tree:
        off = _locate_heading_line(full_text, title)
        if off >= 0:
            offsets.append((off, level, title))

    segments = []
    stack: list[tuple[int, str]] = []  # (level, title)
    for i, (off, level, title) in enumerate(offsets):
        # 父级继承：pop 掉 level >= 当前 level 的帧
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = " > ".join(t for _, t in stack)
        end = offsets[i + 1][0] if i + 1 < len(offsets) else len(full_text)
        segments.append({"path": path, "start": off, "end": end})

    return segments


def _normalize_ws(text: str) -> str:
    """去掉全部空白，用于标题匹配的格式归一化。

    中文无词间空格，pm 标题与 fitz 正文的空白差异（合并/保留、标点邻接空格）
    只需去掉全部空白即可对齐。仅用于比较，不影响原文。

    Args:
        text: 待归一化的标题或行文本

    Returns:
        去空白后的文本
    """
    return re.sub(r"\s+", "", text)


def _locate_heading_line(full_text: str, title: str) -> int:
    """定位标题文本在全文中的行首偏移。

    先按标题文本直接 find（docx 标题是完整段落，可寻址；PDF 带 # 前缀时
    title 是 "# title" 的子串），命中后校验所在行确为标题行并回退到行首——
    这样含 # 前缀的 chunk 也能命中区间。找不到时退化逐行匹配标题文本。

    Args:
        full_text: 文档全文
        title: 标题文本（heading_tree 中的值，已去 # 前缀）

    Returns:
        标题所在行的行首字符偏移；未命中返回 -1。
    """
    pos = full_text.find(title)
    if pos >= 0:
        line_start = full_text.rfind("\n", 0, pos) + 1
        line_end = full_text.find("\n", pos)
        if line_end < 0:
            line_end = len(full_text)
        line = full_text[line_start:line_end].strip()
        # 该行是标题行（去 # 后与 title 一致）才算命中，避免匹配到正文里的同名文本
        if line.lstrip("#").strip() == title:
            return line_start

    # 退化：去全部空白归一化逐行匹配（覆盖 pm 标题 vs fitz 正文的空白格式差异）
    norm_title = _normalize_ws(title)
    line_start = 0
    for line in full_text.split("\n"):
        stripped = line.strip()
        if _normalize_ws(stripped.lstrip("#").strip()) == norm_title:
            return line_start
        line_start += len(line) + 1
    return -1


def locate_heading_path(content: str, full_text: str, segments: list[dict]) -> str:
    """反推 chunk 内容所属的标题段路径。

    Args:
        content: chunk 内容
        full_text: 文档全文
        segments: build_heading_segments 的返回值

    Returns:
        标题段路径字符串，未命中返回空串。
    """
    if not segments:
        return ""
    pos = full_text.find(content)
    if pos < 0:
        return ""
    end = pos + len(content)
    for seg in segments:
        if seg["start"] <= pos and end <= seg["end"]:
            return seg["path"]
    # 兜底：取包含 pos 的最近段
    for seg in segments:
        if seg["start"] <= pos < seg["end"]:
            return seg["path"]
    return ""
