#!/usr/bin/env python3
"""查询改写方案 A/B 对比脚本 — 捆绑（classify 内输出改写）vs 独立（rewrite 单独调用）。

对固定样本集分别执行两种改写方案，随后对改写结果跑检索 + rerank，
打印改写文本、route、命中情况与 token 成本对比表，用于决策选型。

方案 A（捆绑）：classify 一次 LLM 调用，同时输出 route / missing_entities /
confidence 与改写字段（medium → standalone_query，complex → sub_queries）。

方案 B（独立）：classify 输出 route（现状 prompt），medium / complex 时
再单独一次 LLM 调用做改写（单任务 prompt）。

用法：
    python -m src.cli.compare_rewrite --kb-name test123

前提：
  - 目标 KB 已入库，ChromaDB 中已有向量
  - .env 配置了 DashScope API Key（LLM / Embedding / Rerank）
"""

import argparse
import asyncio
import json
import sys

from loguru import logger

from src.config import CLASSIFIER_TEMPERATURE, RERANK_MIN_SCORE, TOP_K_RETRIEVAL
from src.config.prompts import CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_TEMPLATE
from src.core.logging import setup_logging
from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import KbRepo
from src.infra.db.vector_store import VectorStore
from src.infra.llm.chat_message import ChatMessage
from src.infra.search.complexity_scorer import score_complexity
from src.infra.search.entity_extractor import EntityExtractor
from src.infra.search.query_router import aggregate_kb_entities
from src.models import get_classify_llm, get_rerank

# ====== 方案 A：捆绑版 classify prompt ======

BUNDLED_CLASSIFY_SYSTEM_PROMPT: str = """你是一个查询分析专家。分析用户查询的复杂度、补充缺失实体、确定路由，并输出改写后的查询。

输入包含：
- 用户问题
- 已提取实体列表（正则匹配，可能为空）
- 知识库候选实体（当前 KB 文档中聚合到的公司/报告期/代码，供追问设计参考）
- 复杂度评分（规则预判，仅供参考）
- 对话历史（多轮上下文）

分析任务：
1. 确定路由（route）：simple / medium / complex
2. 补充缺失实体：检查 query 中是否缺少关键信息（年份、公司、指标等）
3. 评估置信度：0~1
4. 改写查询（仅当 medium 或 complex 时输出）：
   - medium: 输出 standalone_query —— 结合对话历史补全缺失实体，形成一条可独立检索的完整查询
   - complex: 输出 sub_queries —— 2-4 条可独立检索的子查询列表，覆盖对比/多步分析的每个侧面

规则：
- simple: 问候、感谢、单一事实查询，不需要检索或仅需简单检索
- medium: 需单次检索的事实性问题（"2024年营收多少"）
- complex: 需多步推理、对比、因果关系分析（"对比A和B的差异"）
- 如果 query 缺少关键信息（如 "营收多少" 缺年份，但历史没提），标记为 missing_entities
- 如果缺失实体可以从对话历史中推断，不要标记为缺失
- 改写规则（重要）：
  - 只在对话历史提供了明确约束时，才把约束（年份/公司/期间）补进改写查询
  - 严禁修改用户问题中已有的数字、公司名、期间、否定词
  - 保持原语言（中文），输出成句、可直接用于检索的完整查询
  - 如果当前问题本身已完整，standalone_query 可原样返回

只返回 JSON，不要包含其他内容。"""

BUNDLED_CLASSIFY_USER_TEMPLATE: str = """用户问题：{query}

已提取实体（正则）：
{entities}

知识库候选实体（供追问参考）：
{kb_entities}

复杂度评分（规则预判）：{complexity_score}

对话历史（最近2轮）：
{history}

输出 JSON（严格按此格式，改写字段仅对应路由输出）：
{{
  "route": "simple|medium|complex",
  "missing_entities": [
    {{"type": "year", "question": "请问您想查询哪一年的数据？"}}
  ],
  "confidence": 0.0,
  "standalone_query": "仅 medium 时输出；simple/complex 省略",
  "sub_queries": ["仅 complex 时输出 2-4 条；simple/medium 省略"]
}}
"""

# ====== 方案 B：独立版 rewrite prompt ======

INDEPENDENT_REWRITE_SYSTEM_PROMPT: str = """你是一个查询改写专家。把当前用户问题改写为可独立检索的完整查询。

输入包含：
- 当前用户问题
- 路由类型：medium（单条改写）或 complex（多条子查询分解）
- 对话历史（多轮上下文，最近 2 轮）

任务：
- medium: 输出 standalone_query（单条，结合对话历史补全缺失实体）
- complex: 输出 sub_queries（2-4 条子查询，覆盖对比/多步分析的每个侧面）

规则（重要）：
- 只在对话历史提供了明确约束时，才把约束（年份/公司/期间）补进改写查询
- 严禁修改用户问题中已有的数字、公司名、期间、否定词
- 保持原语言（中文），输出成句、可直接用于检索的完整查询
- 如果当前问题本身已完整，standalone_query 可原样返回

只返回 JSON，不要包含其他内容。"""

INDEPENDENT_REWRITE_USER_TEMPLATE: str = """用户问题：{query}

路由类型：{route}

对话历史（最近2轮）：
{history}

输出 JSON（严格按此格式，改写字段仅对应路由输出）：
{{
  "standalone_query": "仅 medium 时输出；complex 省略",
  "sub_queries": ["仅 complex 时输出 2-4 条；medium 省略"]
}}
"""


class RewriteSample:
    """单条对比样本。

    sid: 样本编号
    query: 当前用户问题
    history: 对话历史（ChatMessage 列表）
    gold_route: 期望路由（medium / complex）
    gold_standalone: 期望单条改写（medium 样本）
    gold_sub_queries: 期望子查询列表（complex 样本）
    targets: 命中判定目标，元素为 (doc_id 前缀, 命中关键词)，关键词可为空串
    note: 样本说明（期望/关注点）
    """

    def __init__(
        self,
        sid: str,
        query: str,
        history: list[ChatMessage],
        gold_route: str,
        gold_standalone: str | None,
        gold_sub_queries: list[str] | None,
        targets: list[tuple[str, str]],
        note: str,
    ) -> None:
        self.sid = sid
        self.query = query
        self.history = history
        self.gold_route = gold_route
        self.gold_standalone = gold_standalone
        self.gold_sub_queries = gold_sub_queries
        self.targets = targets
        self.note = note


SAMPLES: list[RewriteSample] = [
    RewriteSample(
        sid="s1",
        query="毛利率呢",
        history=[
            ChatMessage(role="user", content="2025年第一季度营收多少"),
            ChatMessage(role="assistant", content="2025年第一季度营收为 XX 亿元。"),
        ],
        gold_route="medium",
        gold_standalone="2025年第一季度毛利率",
        gold_sub_queries=None,
        targets=[("29d5f1bb", "毛利率")],
        note="约束保护：历史含 2025Q1；neusoft Q1 报告无毛利率词，预期改写保留日期约束、检索 miss",
    ),
    RewriteSample(
        sid="s2",
        query="毛利率呢",
        history=[
            ChatMessage(role="user", content="腾讯2024年营收多少"),
            ChatMessage(role="assistant", content="腾讯2024年营收为 XX 亿元。"),
        ],
        gold_route="medium",
        gold_standalone="腾讯2024年毛利率",
        gold_sub_queries=None,
        targets=[("38e82f97", "毛利率")],
        note="关键样本：期望补全公司+年份；已知带约束改写后 rerank<0.3，暴露阈值问题",
    ),
    RewriteSample(
        sid="s3",
        query="对比一下腾讯和东软的利润",
        history=[],
        gold_route="complex",
        gold_standalone=None,
        gold_sub_queries=["腾讯利润", "东软利润"],
        targets=[("38e82f97", "利润"), ("29d5f1bb", "利润")],
        note="对比类分解：无历史",
    ),
    RewriteSample(
        sid="s4",
        query="腾讯和东软谁毛利率更高",
        history=[],
        gold_route="complex",
        gold_standalone=None,
        gold_sub_queries=["腾讯毛利率", "东软毛利率"],
        targets=[("38e82f97", "毛利率")],
        note="对比类：东软无毛利率词，预期只命中腾讯侧",
    ),
    RewriteSample(
        sid="s5",
        query="他们的营收呢",
        history=[
            ChatMessage(role="user", content="介绍一下腾讯"),
            ChatMessage(role="assistant", content="腾讯是知名互联网公司。"),
        ],
        gold_route="medium",
        gold_standalone="腾讯营收",
        gold_sub_queries=None,
        targets=[("38e82f97", "营收")],
        note="代词指代补全",
    ),
    RewriteSample(
        sid="s6",
        query="2024年呢",
        history=[
            ChatMessage(role="user", content="2023年净利润多少"),
            ChatMessage(role="assistant", content="2023年净利润为 XX。"),
        ],
        gold_route="medium",
        gold_standalone="2024年净利润",
        gold_sub_queries=None,
        targets=[("38e82f97", "净利")],
        note="期间补全：不篡改指标",
    ),
]


def _format_history(history: list[ChatMessage]) -> str:
    """将对话历史格式化为用户/助手行文本，最近 4 条。"""
    lines = []
    for msg in history[-4:]:
        role = "用户" if msg.role == "user" else "助手"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


async def _resolve_kb_id(kb_name: str) -> str:
    """将知识库名称解析为 UUID，未找到时抛出 ValueError。"""
    repo = KbRepo(session_factory)
    for kb in await repo.get_all_kb():
        if kb.name == kb_name:
            return kb.id
    raise ValueError(f"Knowledge base '{kb_name}' not found")


def _llm_json(llm, prompt: str) -> tuple[dict, int, int]:
    """调用 LLM 并解析 JSON 输出。

    Args:
        llm: ChatOpenAI 实例
        prompt: 完整 prompt 文本

    Returns:
        (解析后的 dict, prompt_tokens, completion_tokens)；解析失败返回 ({}, 0, 0)
    """
    from langchain_core.messages import HumanMessage

    try:
        response = llm.invoke(
            [HumanMessage(content=prompt)], temperature=CLASSIFIER_TEMPERATURE
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM invoke failed: {}", e)
        return {}, 0, 0
    raw = (response.content or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        logger.warning("LLM JSON parse failed: {} raw={}", e, raw[:200])
        return {}, 0, 0
    metadata = getattr(response, "response_metadata", {}) or {}
    usage = metadata.get("token_usage", {})
    prompt_t = usage.get("prompt_tokens")
    completion_t = usage.get("completion_tokens")
    if prompt_t is None or completion_t is None:
        usage_meta = getattr(response, "usage_metadata", None) or {}
        prompt_t = usage_meta.get("input_tokens")
        completion_t = usage_meta.get("output_tokens")
    return data, int(prompt_t or 0), int(completion_t or 0)


def _run_classify(
    llm,
    query: str,
    entities_text: str,
    complexity_score: float,
    history_text: str,
    kb_entities: str,
    system_prompt: str,
    user_template: str,
) -> tuple[dict, int, int]:
    """执行一次 classify LLM 调用，返回 (解析结果, prompt_tokens, completion_tokens)。

    解析结果含 route / missing_entities / confidence，以及捆绑版特有的
    standalone_query / sub_queries 字段（若无则缺省）。
    """
    prompt = (
        f"{system_prompt}\n\n"
        f"{user_template.format(query=query, entities=entities_text or '无', kb_entities=kb_entities or '无', complexity_score=str(complexity_score), history=history_text or '无')}"
    )
    data, pt, ct = _llm_json(llm, prompt)
    return (
        {
            "route": data.get("route", "medium"),
            "missing_entities": data.get("missing_entities", []),
            "confidence": data.get("confidence", 0.0),
            "standalone_query": data.get("standalone_query"),
            "sub_queries": data.get("sub_queries"),
        },
        pt,
        ct,
    )


def _run_rewrite(
    llm, query: str, route: str, history_text: str
) -> tuple[dict, int, int]:
    """执行一次独立 rewrite LLM 调用，返回 (解析结果, prompt_tokens, completion_tokens)。"""
    prompt = (
        f"{INDEPENDENT_REWRITE_SYSTEM_PROMPT}\n\n"
        f"{INDEPENDENT_REWRITE_USER_TEMPLATE.format(query=query, route=route, history=history_text or '无')}"
    )
    return _llm_json(llm, prompt)


def _extract_rewrite_queries(result: dict, fallback_query: str) -> list[str]:
    """从 classify/rewrite 结果提取有效改写查询列表，无有效改写时回退到原 query。

    优先级：sub_queries（过滤空串）→ standalone_query（单条）→ 原 query。
    """
    subs = result.get("sub_queries")
    if isinstance(subs, list):
        valid = [q for q in subs if isinstance(q, str) and q.strip()]
        if valid:
            return valid
    sq = result.get("standalone_query")
    if isinstance(sq, str) and sq.strip():
        return [sq]
    return [fallback_query]


async def _score_queries(
    queries: list[str], kb_id: str, store: VectorStore, reranker
) -> list[tuple]:
    """对每个改写查询执行 dense 检索 + rerank，合并返回打分列表。

    Returns:
        list[tuple] 按分数降序，元素为
        (relevance_score, doc_id, source, content, chunk_id)
    """
    merged: list[tuple] = []
    seen: set[str] = set()
    for q in queries:
        try:
            results = await asyncio.to_thread(
                store.similarity_search, kb_id, q, TOP_K_RETRIEVAL
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("search failed query={}: {}", q, e)
            continue
        docs = [r.content for r in results]
        try:
            reranked = reranker.rerank(docs, q)
        except Exception as e:  # noqa: BLE001
            logger.warning("rerank failed query={}: {}", q, e)
            continue
        for item in reranked:
            r = results[item["index"]]
            score = item.get("relevance_score", 0.0)
            if r.id in seen:
                continue
            seen.add(r.id)
            merged.append(
                (
                    score,
                    r.metadata.get("doc_id", ""),
                    r.metadata.get("source", ""),
                    (r.content or "").strip(),
                    r.id,
                )
            )
    merged.sort(key=lambda x: x[0], reverse=True)
    return merged[:10]


def _hit_summary(
    scored: list[tuple], targets: list[tuple[str, str]]
) -> list[tuple[str, str, tuple | None]]:
    """判定每个目标的命中情况。

    Returns:
        list[(doc_id 前缀, 关键词, (分数, source) | None)]
    """
    summary = []
    for prefix, kw in targets:
        found = None
        for score, doc_id, source, content, _cid in scored:
            if doc_id.startswith(prefix) and (not kw or kw in content):
                found = (score, source)
                break
        summary.append((prefix, kw, found))
    return summary


def _fmt_hit(summary: list[tuple[str, str, tuple | None]]) -> str:
    """格式化命中判定结果。"""
    parts = []
    for prefix, kw, found in summary:
        mark = f"√ {found[0]:.3f}" if found else "×"
        parts.append(f"{prefix[:8]}({kw or '*'}){mark}")
    return "  ".join(parts)


def _fmt_queries(queries: list[str]) -> str:
    """格式化改写查询列表。"""
    if len(queries) == 1:
        return f'"{queries[0]}"'
    return "[" + ", ".join(f'"{q}"' for q in queries) + "]"


def _gold_text(s: RewriteSample) -> str:
    """返回样本期望改写文本。"""
    if s.gold_sub_queries is not None:
        return " | ".join(s.gold_sub_queries)
    return s.gold_standalone or ""


def _print_sample(
    s: RewriteSample,
    a_result: dict,
    a_queries: list[str],
    a_scored: list[tuple],
    a_pt: int,
    a_ct: int,
    b_result: dict,
    b_queries: list[str],
    b_scored: list[tuple],
    b_pt: int,
    b_ct: int,
    b_rw_pt: int,
    b_rw_ct: int,
) -> None:
    """打印单个样本的 A/B 对比结果。"""
    print("=" * 100)
    print(f"[{s.sid}] query={s.query!r} history={[m.content for m in s.history]}")
    print(f"    gold(route={s.gold_route}): {_gold_text(s)}")
    print(f"    note: {s.note}")
    print(f"    targets: {_fmt_hit(_hit_summary(a_scored, s.targets))}")
    route_a = a_result.get("route", "?")
    route_b = b_result.get("route", "?")
    route_same = "SAME" if route_a == route_b else "DIFF"
    print(
        f"  [A 捆绑] route={route_a} 改写={_fmt_queries(a_queries)} "
        f"tokens={a_pt}+{a_ct}"
    )
    print(f"           hit: {_fmt_hit(_hit_summary(a_scored, s.targets))}")
    print(f"           top5: {_top_scores(a_scored)}")
    print(
        f"  [B 独立] route={route_b} 改写={_fmt_queries(b_queries)} "
        f"tokens={b_pt}+{b_ct} + rw={b_rw_pt}+{b_rw_ct} ({route_same})"
    )
    print(f"           hit: {_fmt_hit(_hit_summary(b_scored, s.targets))}")
    print(f"           top5: {_top_scores(b_scored)}")


def _top_scores(scored: list) -> list[float]:
    """提取打分列表的前 5 个分数。"""
    if not scored:
        return []
    return [round(x[0], 3) for x in scored[:5]]


async def main() -> None:
    """入口：解析参数、加载 KB、遍历样本输出对比表。"""
    parser = argparse.ArgumentParser(description="捆绑 vs 独立查询改写对比")
    parser.add_argument("--kb-name", default="test123", help="知识库名称")
    args = parser.parse_args()

    try:
        kb_id = await _resolve_kb_id(args.kb_name)
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)

    store = VectorStore()
    reranker = get_rerank()
    llm = get_classify_llm()
    entity_extractor = EntityExtractor()
    agg = await aggregate_kb_entities([kb_id])
    kb_entities = agg.text

    total_tokens = {"A": [0, 0], "B": [0, 0], "B_rewrite": [0, 0]}
    print(f"KB: {args.kb_name} ({kb_id})  kb_entities={kb_entities[:80]}")
    print(f"RERANK_MIN_SCORE={RERANK_MIN_SCORE} (低于此分数视为不达标)")

    for s in SAMPLES:
        entities = entity_extractor.extract(s.query)
        entities_text = (
            "; ".join(f"{e.type}={e.value}" for e in entities if e.value) or "无"
        )
        complexity_score = score_complexity(s.query, entities)
        history_text = _format_history(s.history)

        # 方案 A：捆绑 classify
        a_result, a_pt, a_ct = _run_classify(
            llm,
            s.query,
            entities_text,
            complexity_score,
            history_text,
            kb_entities,
            BUNDLED_CLASSIFY_SYSTEM_PROMPT,
            BUNDLED_CLASSIFY_USER_TEMPLATE,
        )
        a_queries = _extract_rewrite_queries(a_result, s.query)
        total_tokens["A"][0] += a_pt
        total_tokens["A"][1] += a_ct

        # 方案 B：现状 classify + 独立 rewrite
        b_result, b_pt, b_ct = _run_classify(
            llm,
            s.query,
            entities_text,
            complexity_score,
            history_text,
            kb_entities,
            CLASSIFIER_SYSTEM_PROMPT,
            CLASSIFIER_USER_TEMPLATE,
        )
        total_tokens["B"][0] += b_pt
        total_tokens["B"][1] += b_ct
        b_rw_pt = b_rw_ct = 0
        if b_result.get("route") in ("medium", "complex"):
            rw_data, b_rw_pt, b_rw_ct = _run_rewrite(
                llm, s.query, b_result.get("route", "medium"), history_text
            )
            b_queries = _extract_rewrite_queries(rw_data, s.query)
            total_tokens["B_rewrite"][0] += b_rw_pt
            total_tokens["B_rewrite"][1] += b_rw_ct
        else:
            b_queries = [s.query]

        a_scored = await _score_queries(a_queries, kb_id, store, reranker)
        b_scored = await _score_queries(b_queries, kb_id, store, reranker)

        _print_sample(
            s,
            a_result,
            a_queries,
            a_scored,
            a_pt,
            a_ct,
            b_result,
            b_queries,
            b_scored,
            b_pt,
            b_ct,
            b_rw_pt,
            b_rw_ct,
        )

    # 汇总成本
    print("=" * 100)
    print("[成本汇总] 调用次数 A=6 次 classify | B=6 次 classify + N 次 rewrite")
    print(
        f"  A 总 tokens: {total_tokens['A'][0] + total_tokens['A'][1]} "
        f"(prompt={total_tokens['A'][0]} completion={total_tokens['A'][1]})"
    )
    print(
        f"  B 总 tokens: {total_tokens['B'][0] + total_tokens['B'][1] + total_tokens['B_rewrite'][0] + total_tokens['B_rewrite'][1]} "
        f"(classify prompt={total_tokens['B'][0]} completion={total_tokens['B'][1]}"
        f" | rewrite prompt={total_tokens['B_rewrite'][0]} completion={total_tokens['B_rewrite'][1]})"
    )
    print("[结论] 对比改写文本质量（约束保留/成句/补全）与 route 一致性，再决定选型")


if __name__ == "__main__":
    setup_logging(configure_trace_id=True)
    asyncio.run(main())
