"""查询意图路由器模块 — 三层架构：实体提取 → 复杂度评分 → LLM 分类。"""

import json
import re
from typing import Any

from loguru import logger
from langchain_core.messages import HumanMessage

from src.infra.search.entity_extractor import EntityExtractor
from src.infra.search.complexity_scorer import score_complexity
from src.infra.llm.prompt_manager import PromptManager
from src.agents.graph.state import RAGQueryIntent

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
    def __init__(self, llm=None, prompt_manager: PromptManager | None = None):
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
            return self._simple_result()
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
            "intent": RAGQueryIntent(route=llm_result["route"]),
            "extracted_entities": entities_dict,
            "missing_entities": llm_result.get("missing_entities", []),
            "classification_confidence": llm_result.get("confidence", 0.0),
        }
        self._cache[cleaned] = result
        return result

    def _simple_result(self) -> dict[str, Any]:
        return {
            "intent": RAGQueryIntent(route="simple"),
            "extracted_entities": [],
            "missing_entities": [],
            "classification_confidence": 1.0,
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
            response = self._llm.invoke(messages, temperature=CLASSIFIER_TEMPERATURE)
            raw = response.content.strip()
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
