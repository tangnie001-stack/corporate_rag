"""标题段区间定位器 — 由标题树 + 全文建立标题段区间，反推 chunk 归属。"""


def build_heading_segments(
    full_text: str, heading_tree: list[tuple[int, str]]
) -> list[dict]:
    """由标题树和全文建立标题段区间表。

    Args:
        full_text: 文档全文（Markdown）
        heading_tree: (level, heading) 列表，来自 ParseResult.heading_tree

    Returns:
        标题段区间列表，每项 {"path", "start", "end"}；
        path 为父级拼接的标题路径，start/end 为全文中的字符偏移。
        无标题树时返回空列表。
    """
    if not heading_tree:
        return []

    # 在 full_text 中定位每个标题行的偏移
    offsets = []
    lines = full_text.split("\n")
    pos = 0
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            offsets.append((pos, level, title))
        pos += len(line) + 1  # +1 换行符

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


def locate_heading_path(
    content: str, full_text: str, segments: list[dict]
) -> str:
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
