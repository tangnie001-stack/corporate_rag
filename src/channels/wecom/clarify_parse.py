"""把用户在企微里回复的文本解析成站点同构的澄清答案（design D14）。

站点前端有专用作答 UI（逐问提交结构化 answers）；企微只能用**文本回填**，
故本模块把一段文本映射成 `[{id, selected, custom}]`：
- 单问：命中选项（原文/序号）→ `selected`；否则整段作为 `custom`
- 多问：要求**编号回复**（`1) …` / `1. …` / `1、…`），逐条映射；无法编号 → 判定无效
- `multi_select`：先按顿号/逗号/空格切分再与选项比对

返回 `None` 表示"这段文本不能作为答案"，调用方应提示用户重答且**不消耗**挂起。
"""

from __future__ import annotations

import re

_INDEXED_LINE = re.compile(r"^\s*(\d+)\s*[).、]\s*(.+?)\s*$")
_SEPARATORS = re.compile(r"[、,，;；\s]+")


def _normalize(text: str) -> str:
    """去首尾空白并统一全角空格。"""
    return text.replace("\u3000", " ").strip()


def _match_options(text: str, options: list[str], *, multi: bool) -> list[str]:
    """把文本与选项比对，返回命中的选项（保序、去重）。

    Args:
        text: 用户文本（已 normalize）
        options: 候选项（纯字符串）
        multi: 是否多选

    Returns:
        命中的选项列表；无命中返回空列表
    """
    if not options:
        return []
    candidates = [_normalize(text)]
    lowered = text.lower()
    if multi:
        candidates = [part for part in _SEPARATORS.split(text) if part]
    hits: list[str] = []
    for option in options:
        option_norm = _normalize(option)
        option_lower = option_norm.lower()
        for candidate in candidates:
            if candidate and candidate.lower() == option_lower:
                if option not in hits:
                    hits.append(option)
                break
    if hits:
        return hits
    if len(options) == 1 and lowered == _normalize(options[0]).lower():
        return [options[0]]
    return []


def _select_by_index(text: str, options: list[str]) -> list[str]:
    """纯正整数（1 起）落在选项范围内时返回该选项，否则空列表。

    Args:
        text: 用户文本（已 normalize）
        options: 候选项（纯字符串）

    Returns:
        命中的单元素列表；索引越界或非纯数字时返回空列表
    """
    if not text.isdigit() or not options:
        return []
    position = int(text)
    if 1 <= position <= len(options):
        return [options[position - 1]]
    return []


def _answer_for(text: str, question: dict) -> dict:
    """把一段文本映射成单问的答案元素。"""
    options = question.get("options") or []
    option_strs = [str(o) for o in options]
    multi = bool(question.get("multi_select"))
    hits = _match_options(text, option_strs, multi=multi)
    if hits:
        return {"id": str(question.get("id", "")), "selected": hits, "custom": ""}
    if not multi:
        indexed = _select_by_index(text, option_strs)
        if indexed:
            return {
                "id": str(question.get("id", "")),
                "selected": indexed,
                "custom": "",
            }
    return {"id": str(question.get("id", "")), "selected": [], "custom": text}


def parse_answers(text: str, questions: list) -> list[dict] | None:
    """把企微文本回复解析成站点同构答案列表。

    Args:
        text: 用户回复的原始文本
        questions: 渲染时使用的澄清问题清单（元素含 id/question/options/multi_select）

    Returns:
        站点同构答案列表；无法解析时返回 None
    """
    normalized = _normalize(text)
    if not normalized or not questions:
        return None
    valid = [q for q in questions if isinstance(q, dict) and str(q.get("id", ""))]
    if not valid:
        return None
    if len(valid) == 1:
        return [_answer_for(normalized, valid[0])]

    numbered: dict[int, str] = {}
    for line in normalized.splitlines():
        matched = _INDEXED_LINE.match(line)
        if matched is None:
            continue
        position = int(matched.group(1))
        numbered[position] = _normalize(matched.group(2))
    if not numbered:
        return None
    answers: list[dict] = []
    for position, question in enumerate(valid, start=1):
        body = numbered.get(position, "")
        answers.append(_answer_for(body, question))
    return answers
