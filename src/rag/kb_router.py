"""知识库智能路由模块 — 语义路由 + LLM 兜底。

当用户选择"所有知识库"时，先用 Embedding 相似度匹配最相关的 1-2 个 KB，
低置信度时使用 LLM 进行分类兜底。
"""

import numpy as np
from loguru import logger

from src.infra.db.models.kb import KbModel as KbListItem


# 语义路由相似度阈值：低于此值触发 LLM 兜底
SEMANTIC_THRESHOLD = 0.82


class KBRouter:
    """知识库路由器 — 根据用户查询匹配最相关的知识库。

    用法:
        router = KBRouter(embed_fn, llm)
        matched_ids = router.route(query, kb_list)
    """

    def __init__(self, embed_fn, llm=None):
        """初始化路由器。

        Args:
            embed_fn: 嵌入函数，需实现 embed_query(text) -> list[float]
            llm: 可选，LLM 实例（用于低置信度兜底），需实现 invoke() 接口
        """
        self._embed_fn = embed_fn
        self._llm = llm

    def route(self, query: str, kb_list: list[KbListItem]) -> list[str]:
        """执行路由决策，返回匹配的 kb_id 列表。

        策略：
        1. 计算 query 与每个 KB name+description 的余弦相似度
        2. 取 top-1 相似度
        3. top-1 相似度 >= 阈值 → 取 top-2 KB IDs
        4. top-1 相似度 < 阈值 → LLM 兜底（如 llm 可用）
        5. LLM 兜底失败或无 llm → 返回空列表（外层降级为全量检索）

        Args:
            query: 用户查询文本
            kb_list: 用户的所有知识库列表（含 name 和 description）

        Returns:
            list[str]: 匹配的 kb_id 列表，空列表表示未命中（需降级）
        """
        if not kb_list:
            logger.info("KBRouter: empty kb_list, returning []")
            return []

        # ── 1. 构建每个 KB 的 route_key ──
        route_keys = []
        for kb in kb_list:
            key = kb.name
            if kb.description:
                key = f"{kb.name}: {kb.description}"
            route_keys.append(key)

        # ── 2. 计算 query embedding ──
        query_vec = np.array(self._embed_fn.embed_query(query))

        # ── 3. 计算每个 KB 的 embedding（批量）──
        # TODO: 当 KB 数量超过 100 时需引入 FAISS 索引缓存 KB 向量，避免实时计算延迟
        # 此处不缓存，每次实时计算（KB 数量少时可接受）
        kb_vecs = []
        for key in route_keys:
            vec = np.array(self._embed_fn.embed_query(key))
            kb_vecs.append(vec)

        # ── 4. 计算余弦相似度 ──
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            logger.warning("KBRouter: query embedding is zero vector")
            return self._llm_fallback(query, kb_list)

        similarities = []
        for vec in kb_vecs:
            vec_norm = np.linalg.norm(vec)
            if vec_norm == 0:
                similarities.append(0.0)
            else:
                sim = float(np.dot(query_vec, vec) / (query_norm * vec_norm))
                similarities.append(sim)

        # ── 5. 排序 ──
        ranked = sorted(
            zip(kb_list, similarities),
            key=lambda x: x[1],
            reverse=True,
        )

        top_kb, top_score = ranked[0]
        logger.info(
            "KBRouter semantic: top={}({}) score={:.4f} threshold={}",
            top_kb.name, top_kb.id[:8], top_score, SEMANTIC_THRESHOLD,
        )

        if top_score >= SEMANTIC_THRESHOLD:
            # 取 top-2
            matched = [item[0].id for item in ranked[:2]]
            logger.info("KBRouter: semantic match -> {}", matched)
            return matched

        # ── 6. 低置信度 → LLM 兜底 ──
        return self._llm_fallback(query, kb_list)

    def _llm_fallback(
        self, query: str, kb_list: list[KbListItem]
    ) -> list[str]:
        """使用 LLM 分类查询归属的知识库。

        Returns:
            list[str]: LLM 选中的 kb_id 列表（最多 2 个），
                       出错或无 llm 时返回空列表
        """
        if not self._llm:
            logger.info("KBRouter: no LLM available, returning []")
            return []

        kb_options = "\n".join(
            f"- {kb.id}: {kb.name}" + (f" ({kb.description})" if kb.description else "")
            for kb in kb_list
        )

        prompt = f"""根据用户问题，选择最相关的 1-2 个知识库。
只返回知识库 ID，多个用逗号分隔，不要包含其他内容。

可选知识库：
{kb_options}

用户问题：{query}"""

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            response = self._llm.invoke([
                SystemMessage(content="你是一个知识库路由专家。返回最相关的知识库 ID。"),
                HumanMessage(content=prompt),
            ])
            raw = response.content.strip()
            ids = [id_str.strip() for id_str in raw.split(",") if id_str.strip()]
            valid_ids = {kb.id for kb in kb_list}
            matched = [id_str for id_str in ids if id_str in valid_ids][:2]
            logger.info("KBRouter: LLM fallback -> {}", matched)
            return matched
        except Exception as e:
            logger.warning("KBRouter: LLM fallback failed: {}", e)
            return []
