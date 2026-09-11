"""`/xxx` 前缀解析与读时清洗（design D22/D25）。

解析（生成入口用）：行首 `/name` 且 name 形如 ASCII slug ⇒ 命令形态；
  命中注册表 ⇒ known（task = 去掉前缀后的文本）；
  未命中 ⇒ unknown（必须显式告知用户，不静默降级为普通文本）。
清洗（组装 prompt 前用）：只剥**已注册**技能的 `/name ` 前缀，其余原样返回；
  与解析共用同一实现，保证"能触发的才清洗"口径一致。落库保留原文。
"""

import re
from dataclasses import dataclass

from src.agents.skills.models import SkillRecord

PREFIX_PATTERN = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+([\s\S]*))?$")
"""命令形态：行首 / + ASCII slug + 可选空白 + 其余任务文本。"""


@dataclass
class PrefixParse:
    """一次前缀解析的结果。

    kind: 三态 —— plain（非命令）/ known（命中）/ unknown（形如命令但未注册）
    skill_name: 解析出的技能名（kind=plain 时为空串）
    task: 任务文本（known 为去前缀后的剩余文本；plain 为原文）
    record: 命中的 SkillRecord（仅 kind=known 非空）
    """

    kind: str  # plain / known / unknown
    skill_name: str  # 解析出的技能名
    task: str  # 任务文本
    record: SkillRecord | None  # 命中的技能记录


def parse_prefix(text: str, known_names: set[str], registry) -> PrefixParse:
    """解析行首 `/name` 前缀。

    Args:
        text: 用户输入原文
        known_names: 已注册技能的可见名集合（用于三态判定）
        registry: 技能注册表（仅 kind=known 时按 name 取 SkillRecord）

    Returns:
        PrefixParse；非命令形态返回 kind="plain" 且 task 为原文
    """
    match = PREFIX_PATTERN.match(text)
    if match is None:
        return PrefixParse(kind="plain", skill_name="", task=text, record=None)
    name = match.group(1)
    rest = match.group(2)
    if rest is None:
        rest = ""
    if name not in known_names:
        return PrefixParse(kind="unknown", skill_name=name, task=rest, record=None)
    record = registry.get(name)
    if record is None:
        return PrefixParse(kind="unknown", skill_name=name, task=rest, record=None)
    return PrefixParse(kind="known", skill_name=name, task=rest, record=record)


def clean_prefix(text: str, known_names: set[str]) -> str:
    """剥掉行首**已注册**技能的 `/name ` 前缀（design D25）。

    Args:
        text: 待清洗文本（当前轮 query 或历史 user 消息）
        known_names: 已注册技能的可见名集合

    Returns:
        去前缀后的文本；非命令形态或未注册前缀原样返回（保证幂等）
    """
    match = PREFIX_PATTERN.match(text)
    if match is None:
        return text
    if match.group(1) not in known_names:
        return text
    rest = match.group(2)
    if rest is None:
        return ""
    return rest
