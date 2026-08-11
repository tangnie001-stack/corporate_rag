"""查询意图路由器模块 — 三层架构：实体提取 → 复杂度评分 → LLM 分类。"""

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from loguru import logger

from src.agents.graph.state import LangGraphNode, RAGQueryIntent
from src.infra.llm.prompt_manager import PromptManager
from src.infra.search.complexity_scorer import score_complexity
from src.infra.search.entity_extractor import EntityExtractor

_GREETING_PATTERNS = [
    r"^(你好|您好|hi|hello|thanks|谢谢)$",
]
_SHORT_QUERY_THRESHOLD = 2

SUGGESTIONS_MAP: dict[str, list[str]] = {
    "year": ["2023年", "2024年", "其他"],
    "quarter": ["一季度", "二季度", "三季度", "四季度"],
    "month": ["1月", "12月", "其他"],
    "company": ["腾讯", "阿里巴巴", "其他"],
    "metric": ["营收", "利润", "毛利率", "其他"],
    "default": ["请补充说明", "其他"],
}


@dataclass
class KbEntityAggregate:
    """KB 候选实体聚合结果。

    text: 供 classifier prompt 注入的格式化字符串（无候选时为"无"）
    companies: 聚合到的公司名列表（去重排序，可为空）
    periods: 聚合到的报告期列表（去重排序，可为空）
    codes: 聚合到的证券代码列表（去重排序，可为空）
    """

    text: str
    companies: list[str]
    periods: list[str]
    codes: list[str]

    def to_suggestions(self) -> dict[str, list[str]]:
        """将聚合实体转换为 clarify 追问的 suggestions 映射。

        Returns:
            {实体类型: 候选列表}，与 SUGGESTIONS_MAP 同构；
            每种类型末位追加"其他"快捷项，空类型不出现在结果中
        """
        result: dict[str, list[str]] = {}
        if self.companies:
            result["company"] = list(self.companies) + ["其他"]
        if self.periods:
            result["report_period"] = list(self.periods) + ["其他"]
        if self.codes:
            result["sec_code"] = list(self.codes) + ["其他"]
        return result

    @classmethod
    def empty(cls) -> "KbEntityAggregate":
        """返回空聚合（text="无"，无任何候选），供无 KB/跳过/降级场景使用。"""
        return cls(text="无", companies=[], periods=[], codes=[])


def needs_kb_entities(query: str) -> bool:
    """预判当前查询是否需要 KB 候选实体（与 route 的短路条件一致）。

    空查询/问候/超短查询（≤2 字符）会在 route() 中提前返回 simple，
    用不到 kb_entities 注入，调用方可据此跳过聚合查库。

    Args:
        query: 用户原始查询文本

    Returns:
        True 表示需要聚合 KB 候选；False 表示 route 必然短路、无需候选
    """
    cleaned = query.strip()
    if not cleaned:
        return False
    if any(re.match(p, cleaned) for p in _GREETING_PATTERNS):
        return False
    return len(cleaned) > _SHORT_QUERY_THRESHOLD


def _normalize_entity_value(value: Any) -> str:
    """将实体值归一为字符串。

    meta_info 中实体值理论上是字符串，但防御性处理：
    list 用顿号连接元素，其余类型直接 str（罕见脏数据不阻塞聚合）。

    Args:
        value: 实体字段原始值

    Returns:
        归一后的字符串
    """
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    return str(value)


def _parse_meta_info(doc) -> Any:
    """解析文档 meta_info JSON，损坏时返回 None 并告警跳过该文档。

    Args:
        doc: DocumentRepo 返回的文档对象

    Returns:
        解析后的 JSON 值；JSON 非法或类型错误时返回 None
    """
    try:
        return json.loads(doc.meta_info or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "aggregate_kb_entities: skip doc with bad meta_info, doc_id={}",
            getattr(doc, "id", "?"),
        )
        return None


async def aggregate_kb_entities(kb_ids: list[str] | None) -> KbEntityAggregate:
    """从 KB 文档的 meta_info 聚合候选实体（公司/报告期/代码）。

    遍历每个 KB 下未删除文档的 meta_info["entities"]，去重后返回
    格式化文本（供 classifier prompt 注入）与分类候选（供 clarify 建议）。
    任一文档 meta_info 损坏会被跳过；DB 查询/解析整体失败时降级为
    空聚合（text="无"），保证 classify 路径不被 MySQL 故障击穿。

    Args:
        kb_ids: KB ID 列表；为空或 None 时返回空聚合（text="无"）

    Returns:
        KbEntityAggregate：含格式化文本与三类候选实体列表
    """
    if not kb_ids:
        return KbEntityAggregate.empty()
    companies: set[str] = set()
    periods: set[str] = set()
    codes: set[str] = set()
    try:
        from src.infra.db.engine import session_factory
        from src.infra.db.mysql_db import DocumentRepo

        repo = DocumentRepo(session_factory)
        for kb_id in kb_ids:
            docs = await repo.get_documents(kb_id)
            for d in docs:
                meta = _parse_meta_info(d)
                if not isinstance(meta, dict):
                    continue
                entities = meta.get("entities") or {}
                if not isinstance(entities, dict):
                    entities = {}
                if entities.get("company"):
                    companies.add(_normalize_entity_value(entities["company"]))
                if entities.get("report_period"):
                    periods.add(_normalize_entity_value(entities["report_period"]))
                if entities.get("sec_code"):
                    codes.add(_normalize_entity_value(entities["sec_code"]))
    except Exception as e:  # noqa: BLE001
        logger.warning("aggregate_kb_entities failed, degrade to empty: {}", e)
        return KbEntityAggregate.empty()
    parts = []
    if companies:
        parts.append("公司: " + "、".join(sorted(companies)))
    if periods:
        parts.append("报告期: " + "、".join(sorted(periods)))
    if codes:
        parts.append("代码: " + "、".join(sorted(codes)))
    if parts:
        text = "; ".join(parts)
    else:
        text = "无"
    return KbEntityAggregate(
        text=text,
        companies=sorted(companies),
        periods=sorted(periods),
        codes=sorted(codes),
    )


def _format_history(history: list) -> str:
    lines = []
    for msg in history[-4:]:
        role = "用户" if getattr(msg, "role", "") == "user" else "助手"
        content = getattr(msg, "content", "") or ""
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _llm_rewrite(
    query: str,
    history: list,
    route: str,
    llm: Any,
) -> tuple[list[str], int, int]:
    """独立 LLM 查询改写，失败回退到规则改写。

    Args:
        query: 用户原始查询
        history: 对话历史（ChatMessage 列表）
        route: "medium" | "complex"
        llm: ChatOpenAI 实例（flash）

    Returns:
        (list[str] 改写查询列表, prompt_tokens, completion_tokens)；
        LLM 失败时回退规则改写结果（expand/condense/decompose），仍无效回退原 query
    """
    from langchain_core.messages import HumanMessage

    from src.config import CLASSIFIER_TEMPERATURE
    from src.config.prompts import REWRITE_SYSTEM_PROMPT, REWRITE_USER_TEMPLATE
    from src.rag.retrieval import rewrite_query

    history_text = _format_history(history)
    if history_text:
        prompt = f"{REWRITE_SYSTEM_PROMPT}\n\n{REWRITE_USER_TEMPLATE.format(query=query, route=route, history=history_text)}"
    else:
        prompt = f"{REWRITE_SYSTEM_PROMPT}\n\n{REWRITE_USER_TEMPLATE.format(query=query, route=route, history='无')}"
    try:
        response = llm.invoke(
            [HumanMessage(content=prompt)], temperature=CLASSIFIER_TEMPERATURE
        )
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[-1] == "```":
                raw = "\n".join(lines[1:-1])
            else:
                raw = "\n".join(lines[1:])
        if raw:
            data = json.loads(raw)
        else:
            data = {}
        # token 统计（复用 _llm_classify 的 metadata 读取模式）
        metadata = getattr(response, "response_metadata", {}) or {}
        usage = metadata.get("token_usage", {})
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if pt is None or ct is None:
            usage_meta = getattr(response, "usage_metadata", None) or {}
            pt = usage_meta.get("input_tokens")
            ct = usage_meta.get("output_tokens")
        pt = int(pt or 0)
        ct = int(ct or 0)
        subs = data.get("sub_queries")
        if isinstance(subs, list):
            valid = [q for q in subs if isinstance(q, str) and q.strip()]
            if valid:
                return valid, pt, ct
        sq = data.get("standalone_query")
        if isinstance(sq, str) and sq.strip():
            return [sq], pt, ct
    except Exception:  # noqa: BLE001
        logger.warning("_llm_rewrite LLM failed, fallback to rules")
    # fallback：规则改写 → 原 query
    rule = rewrite_query(query, history, intent_route=route)
    if isinstance(rule, list):
        valid_rules = [q for q in rule if q]
    else:
        if rule:
            valid_rules = [rule]
        else:
            valid_rules = []
    return valid_rules or [query], 0, 0


class QueryRouter:
    def __init__(self, llm: Any = None, prompt_manager: PromptManager | None = None):
        self._entity_extractor = EntityExtractor()
        self._llm = llm
        self._prompt_manager = prompt_manager or PromptManager()
        self._cache: dict[str, dict[str, Any]] = {}

    def route(
        self,
        query: str,
        history: list | None = None,
        kb_entities: str = "",
    ) -> dict[str, Any]:
        history = history or []
        cleaned = query.strip()
        if not cleaned:
            return self._simple_result()
        if any(re.match(p, cleaned) for p in _GREETING_PATTERNS):
            return self._simple_result()
        if len(cleaned) <= _SHORT_QUERY_THRESHOLD:
            return self._simple_result(skip_retrieval=False)
        if cleaned in self._cache:
            # 缓存 key 仅含 query，不含 kb_entities：实例必须按请求新建，
            # 避免跨请求/跨 KB 的候选实体串扰（classify_node 每次新建 QueryRouter）
            return self._cache[cleaned]
        entities = self._entity_extractor.extract(cleaned)
        entities_dict = [
            {"type": e.type, "value": e.value, "confidence": e.confidence}
            for e in entities
        ]
        complexity_score = score_complexity(cleaned, entities)
        if self._llm:
            llm_result = self._llm_classify(
                cleaned, entities, complexity_score, history, kb_entities
            )
        else:
            llm_result = self._fallback_route(complexity_score)
        result = {
            LangGraphNode.Classify.INTENT: RAGQueryIntent(route=llm_result["route"]),
            LangGraphNode.Classify.EXTRACTED_ENTITIES: entities_dict,
            LangGraphNode.Classify.MISSING_ENTITIES: llm_result.get(
                "missing_entities", []
            ),
            LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE: llm_result.get(
                "confidence", 0.0
            ),
            LangGraphNode.Classify.SKIP_RETRIEVAL: False,
        }
        self._cache[cleaned] = result
        return result

    def _simple_result(self, skip_retrieval: bool = True) -> dict[str, Any]:
        return {
            LangGraphNode.Classify.INTENT: RAGQueryIntent(route="simple"),
            LangGraphNode.Classify.EXTRACTED_ENTITIES: [],
            LangGraphNode.Classify.MISSING_ENTITIES: [],
            LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE: 1.0,
            LangGraphNode.Classify.SKIP_RETRIEVAL: skip_retrieval,
        }

    def _llm_classify(
        self, query, entities, complexity_score, history, kb_entities: str = ""
    ) -> dict[str, Any]:
        entities_text = (
            "; ".join(f"{e.type}={e.value}" for e in entities if e.value)
            if entities
            else "无"
        )
        history_text = _format_history(history)
        prompt = self._prompt_manager.get_classifier_prompt(
            query=query,
            entities=entities_text,
            complexity_score=complexity_score,
            history=history_text,
            kb_entities=kb_entities,
        )
        try:
            from src.config import CLASSIFIER_TEMPERATURE

            messages = [HumanMessage(content=prompt)]
            assert self._llm is not None
            response = self._llm.invoke(messages, temperature=CLASSIFIER_TEMPERATURE)
            raw = response.content.strip()
            metadata = getattr(response, "response_metadata", {}) or {}
            usage = metadata.get("token_usage", {})
            prompt_t = usage.get("prompt_tokens")
            completion_t = usage.get("completion_tokens")
            if prompt_t is None or completion_t is None:
                usage_meta = getattr(response, "usage_metadata", None)
                logger.info(
                    "meta keys: {} token_usage keys: {} usage_metadata: {}",
                    list(metadata.keys()),
                    list(usage.keys()),
                    usage_meta,
                )
                if usage_meta:
                    prompt_t = usage_meta.get("input_tokens")
                    completion_t = usage_meta.get("output_tokens")
            logger.info(
                "QueryRouter classify done: model={} prompt_tokens={} completion_tokens={}",
                getattr(self._llm, "model", ""),
                prompt_t if prompt_t is not None else "?",
                completion_t if completion_t is not None else "?",
            )
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            result = json.loads(raw)
            return {
                "route": result.get("route", "medium"),
                "missing_entities": result.get("missing_entities", []),
                "confidence": result.get("confidence", 0.5),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("QueryRouter LLM classify failed: {}", e)
            return self._fallback_route(complexity_score)

    def _fallback_route(self, complexity_score: float) -> dict[str, Any]:
        if complexity_score >= 3.5:
            route = "complex"
        elif complexity_score >= 1.5:
            route = "medium"
        else:
            route = "simple"
        return {"route": route, "missing_entities": [], "confidence": 0.5}
