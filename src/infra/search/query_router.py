"""查询意图路由器模块 — 三层架构：实体提取 → 复杂度评分 → LLM 分类。"""

import json
import re
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


def _format_history(history: list) -> str:
    lines = []
    for msg in history[-4:]:
        role = "用户" if getattr(msg, "role", "") == "user" else "助手"
        content = getattr(msg, "content", "") or ""
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


class QueryRouter:
    def __init__(self, llm: Any = None, prompt_manager: PromptManager | None = None):
        self._entity_extractor = EntityExtractor()
        self._llm = llm
        self._prompt_manager = prompt_manager or PromptManager()
        self._cache: dict[str, dict[str, Any]] = {}

    def route(self, query: str, history: list | None = None) -> dict[str, Any]:
        history = history or []
        cleaned = query.strip()
        if not cleaned:
            return self._simple_result()
        if any(re.match(p, cleaned) for p in _GREETING_PATTERNS):
            return self._simple_result()
        if len(cleaned) <= _SHORT_QUERY_THRESHOLD:
            return self._simple_result(skip_retrieval=False)
        if cleaned in self._cache:
            return self._cache[cleaned]
        entities = self._entity_extractor.extract(cleaned)
        entities_dict = [
            {"type": e.type, "value": e.value, "confidence": e.confidence}
            for e in entities
        ]
        complexity_score = score_complexity(cleaned, entities)
        if self._llm:
            llm_result = self._llm_classify(
                cleaned, entities, complexity_score, history
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
        self, query, entities, complexity_score, history
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
        except Exception as e:
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
