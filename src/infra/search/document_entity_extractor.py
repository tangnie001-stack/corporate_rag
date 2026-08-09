"""文档级实体抽取器 — 规则层（文件名 + 标题栈）+ LLM 校验兜底。

规则层零成本：文件名正则提取 company/year/quarter；
标题栈规则层复用 financial_rag ContextStack 思路提取 year/quarter/report_type/company/currency。
LLM 兜底层由三态开关 ENTITY_LLM_FALLBACK 控制（off/on/auto，非法值按 auto）。
"""

import json
import re
from collections.abc import Callable

from loguru import logger

from src.config import ENTITY_TEXT_PREFIX_LEN, settings
from src.config.const import (
    ENTITY_FULL_PIPELINE_TYPES,
    ENTITY_OPTIONAL_TYPES,
    ENTITY_TYPES,
)
from src.config.prompts import (
    ENTITY_EXTRACTION_SYSTEM_PROMPT,
    ENTITY_EXTRACTION_USER_TEMPLATE,
)

# 文件名模式: {company}_{year}_{quarter|annual}.pdf
_FILENAME_PATTERN = re.compile(
    r"^(?P<company>[a-zA-Z0-9_]+)_(?P<year>20\d{2})_(?P<period>q[1-4]|annual|yearly)\.\w+$"
)

# 标题栈提取规则（对齐 financial_rag HEADING_EXTRACTORS + sec_code）
_HEADING_EXTRACTORS: list[tuple[str, str, Callable[[re.Match], str] | None]] = [
    (r"(\d{4})\s*年", "year", None),
    (r"第([一二三四])季度", "quarter", None),
    (r"Q([1-4])", "quarter", None),
    (r"(利润表|资产负债表|现金流量表|所有者权益变动表)", "report_type", None),
    (r"([\u4e00-\u9fa5]{2,10}(?:公司|集团|有限))", "company", None),
    (r"(人民币|USD|CNY|美元|欧元|港币)", "currency", None),
]

# 证券代码正则（正文/开头）：证券代码[:：]?\s*(\d{6})
_SEC_CODE_PATTERN = re.compile(r"证券代码[:：]?\s*(\d{6})")

# 报告期正则（正文）：20XX年第X季度 / 20XX年
_REPORT_PERIOD_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*年\s*第(?P<quarter>[1-4一二三四])季度"
)


def extract_from_filename(filename: str) -> dict:
    """从文件名提取实体（company/year/quarter）。

    Args:
        filename: 文档文件名，形如 neusoft_2025_q1.pdf

    Returns:
        entities dict；文件名不匹配时返回空 dict
    """
    m = _FILENAME_PATTERN.match(filename or "")
    if not m:
        return {}
    entities = {
        "company": m.group("company"),
        "year": m.group("year"),
    }
    period = m.group("period")
    if period.startswith("q"):
        entities["quarter"] = period.upper()
    return entities


def extract_from_headings(heading_tree: list[tuple[int, str]]) -> dict:
    """标题栈规则层：顶层（level 1）标题优先，子级（level > 1）仅补缺。

    第一遍扫描 level 1 标题提取文档级实体（year/quarter/report_type/company/currency）；
    第二遍扫描 level > 1 子标题，仅填充顶层未提取到的键。
    防止子标题中的跨年引用（如"上年同期"）覆盖文档级 year。

    Args:
        heading_tree: 标题树，元素为 (级别, 标题文本)

    Returns:
        从标题中提取的实体 dict
    """
    result: dict = {}
    # 第一遍：level 1 为文档级标题，优先级最高
    for level, title in heading_tree or []:
        if level == 1:
            _apply_heading_rules(title, result)
    # 第二遍：level > 1 子标题仅补缺，不覆盖顶层结果
    for level, title in heading_tree or []:
        if level > 1:
            _apply_heading_rules(title, result)
    return result


def _apply_heading_rules(title: str, result: dict) -> None:
    """对单个标题应用标题提取规则，只写入未存在的键。"""
    for pattern, key, transform in _HEADING_EXTRACTORS:
        m = re.search(pattern, title)
        if m:
            if transform:
                value = transform(m)
            else:
                value = m.group(1)
            result.setdefault(key, value)


def _extract_from_text(text: str) -> dict:
    """从正文前缀提取 sec_code / report_period。

    Args:
        text: 文档正文前缀

    Returns:
        提取到的实体 dict
    """
    entities: dict = {}
    m = _SEC_CODE_PATTERN.search(text or "")
    if m:
        entities["sec_code"] = m.group(1)
    m = _REPORT_PERIOD_PATTERN.search(text or "")
    if m:
        year = m.group("year")
        q = m.group("quarter")
        if q.isdigit():
            # 阿拉伯数字（1-4）：Q{num}，如 2025年Q2
            entities["report_period"] = f"{year}年Q{q}"
        else:
            # 中文数字（一二三四）：第X季度，如 2025年第二季度
            entities["report_period"] = f"{year}年第{q}季度"
    return entities


def _should_use_llm(rule_entities: dict) -> bool:
    """按三态开关判断是否需要 LLM 兜底。

    三态：off=纯规则 / on=每文档无条件走 LLM / auto=规则空或缺核心实体才走。
    非法配置值按 auto 处理，避免兜底被静默跳过。
    """
    llm_mode = settings.ENTITY_LLM_FALLBACK
    if llm_mode not in ("on", "off", "auto"):
        logger.warning("Invalid ENTITY_LLM_FALLBACK '{}', treated as auto", llm_mode)
        llm_mode = "auto"
    if llm_mode == "on":
        return True
    if llm_mode == "off":
        return False
    core_missing = [t for t in ENTITY_TYPES if not rule_entities.get(t)]
    return (not rule_entities) or bool(core_missing)


class DocumentEntityExtractor:
    """文档级实体抽取器（三层链路：文件名 → 标题栈 → LLM 兜底）。"""

    def __init__(self, llm=None):
        # CLASSIFY_MODEL 实例，可为空（off 模式不调用）
        self._llm = llm

    def extract(
        self,
        filename: str,
        heading_tree: list[tuple[int, str]],
        text: str,
        file_type: str,
    ) -> dict:
        """执行三层实体抽取，返回合并后的 entities dict。

        Args:
            filename: 文档文件名
            heading_tree: ParseResult.heading_tree
            text: 文档全文（用于 LLM 兜底输入取前缀）
            file_type: "pdf"/"docx"/"txt"

        Returns:
            entities dict，含 core 实体（company/report_period/sec_code）与可选实体
        """
        # ① 文件名正则
        rule_entities: dict = extract_from_filename(filename)

        # ② 标题栈规则层（仅 PDF/docx）
        if file_type in ENTITY_FULL_PIPELINE_TYPES:
            heading_entities = extract_from_headings(heading_tree)
            for k, v in heading_entities.items():
                if k == "company":
                    # 文件名 company 仅作 fallback：标题层命中中文全称时覆盖文件名拉丁名
                    # （标题正则只产出中文名，文件名正则只产出 ASCII 拉丁名）
                    if rule_entities.get("company", "").isascii():
                        rule_entities["company"] = v
                else:
                    rule_entities.setdefault(k, v)

        # 正文正则（sec_code / report_period）
        text_entities = _extract_from_text(text[:ENTITY_TEXT_PREFIX_LEN])
        for k, v in text_entities.items():
            rule_entities.setdefault(k, v)

        # ③ LLM 校验兜底（三态开关，失败降级规则结果）
        if _should_use_llm(rule_entities) and self._llm is not None:
            try:
                llm_entities = self._llm_fallback(
                    filename, heading_tree, text, rule_entities
                )
                if llm_entities:
                    # LLM 结果覆盖规则层同名键；只补不删
                    rule_entities.update(llm_entities)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Entity LLM fallback failed for '{}': {} (using rule result)",
                    filename,
                    e,
                )
        return rule_entities

    def _llm_fallback(
        self,
        filename: str,
        heading_tree: list[tuple[int, str]],
        text: str,
        rule_candidates: dict,
    ) -> dict:
        """调用 CLASSIFY_MODEL 校验规则候选并补全。

        Args:
            filename: 文档文件名
            heading_tree: 文档标题树
            text: 文档正文（取前缀作为 LLM 输入）
            rule_candidates: 规则层候选实体

        Returns:
            LLM 输出的实体 dict，仅保留核心 + 可选实体类型内的键
        """
        heading_text = "\n".join(f"{'#' * lvl} {title}" for lvl, title in heading_tree)
        prefix = text[:ENTITY_TEXT_PREFIX_LEN]
        candidates_text = json.dumps(rule_candidates, ensure_ascii=False)
        prompt = ENTITY_EXTRACTION_USER_TEMPLATE.format(
            filename=filename,
            heading_tree=heading_text or "（无标题结构）",
            text_prefix=prefix,
            rule_candidates=candidates_text,
        )
        from langchain_core.messages import HumanMessage, SystemMessage

        assert self._llm is not None
        resp = self._llm.invoke(
            [
                SystemMessage(content=ENTITY_EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        raw = resp.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[-1] == "```":
                raw = "\n".join(lines[1:-1])
            else:
                raw = "\n".join(lines[1:])
        data = json.loads(raw)
        entities = data.get("entities", {}) or {}
        # 过滤：只保留核心 + 可选类型内的键（可选实体不放宽丢弃）
        allowed = set(ENTITY_TYPES) | set(ENTITY_OPTIONAL_TYPES)
        return {k: v for k, v in entities.items() if k in allowed}
