# Deep Research: hybrid search 的融和放在哪一层（引擎内 vs 应用层）

> 调研日期：2026-09-19 ｜ 研究方式：Firecrawl 官方源检索 + 本仓本地参考项目源码实测
> 触发问题：给 `corporate_rag` 的存储替换选型定 RRF 融合的落点。候选：
> **B = 融合下推 SQL** ｜ **C = 引擎只取数、融合留 Python**
> 提问时限定「不考虑测试因素」——即排除"应用层更好单测"这条论据后，业界主流是哪一种。

---

## Executive Summary

**这个问题没有一个统一的答案，而且"业界主流"要分两层说，两层结论方向相反。**

**引擎/数据库厂商侧：主流是提供**引擎内建**融合，并且官方文档一律**推荐**在库内做。** 本次核查的 10 个系统中 8 个提供引擎内嵌的 RRF 或多路融合（Elasticsearch、OpenSearch、Milvus、Weaviate、Qdrant、Azure AI Search、Vespa、MongoDB Atlas）。**没有任何一篇官方文档建议把融合留在应用层。** 趋势明确。

**但"引擎原生 RRF"普遍带硬限制，这恰恰是应用层融合长期存在的原因。** 最典型的是 **Elasticsearch 官方原句：各 child retriever 在 RRF 公式里权重**必须相等****；Azich AI Search 无显式融合参数、`@search.score` 上界受融合路数约束、子分数需单独 `debug` 请求才返回；Weaviate 的 BM25 侧**没有阈值参数**、且 `alpha` 的实际权重可能与标称值不符，"in some cases can be a pure keyword search"；OpenSearch 把归一化技术放在 search pipeline 里，官方实测三种技术在相同输入下给出**三种不同排名**，并让使用者「用你自己的数据和判断表评估，不要从这些结果里选」。所以"加权 + 运行时按租户可配 + 跨后端语义一致"这三件事，原生能力普遍给不了。

**RAG 应用/产品侧：主流不是"统一放应用层"，而是"引擎有这个能力就用，没有或不够用就自己算"。** 框架核心（LangChain `EnsembleRetriever`、LlamaIndex `fusion_retriever`、Haystack `DocumentJoiner`）一律在应用层实现；但存在明确的**下推集成**——LlamaIndex 的 Milvus 集成生成 `RRFRanker(k=60)` / `WeightedRanker` 交给 `client.hybrid_search`，LangChain 的 MongoDB Atlas 集成把 `$rankFusion` 拼进聚合管道。同时存在**明知能下推却不下推**的反例：Haystack 的 ES/OpenSearch 集成、以及本仓参考项目 **WeKnora**（有 OpenSearch 后端、OpenSearch 2.19 已有原生 RRF，它却把加权 RRF 写在 Go 里）。RAGFlow 甚至**按引擎分流**：Infinity 走 `FusionExpr(weighted_sum)` 下推，ES 路径改为单查询 boost + 应用层重排。

**对本项目的直接结论：pgvector 恰恰是"没有原生融合"的那一个。** pgvector 官方 README 的 Hybrid Search 节只说「**You can use** Reciprocal Rank Fusion or a cross-encoder to combine results」——告知可行，不提供实现；ParadeDB `pg_search` 到最新版 **0.25.9（2026-09-11）** 仍把 `Native Hybrid Search` 标为 `coming soon`。因此在 Postgres 上"把融合下推"并不等于"利用引擎能力"，而是**把 RRF 公式手写进 SQL 字符串**（ParadeDB、Apache Doris、Azure HorizonDB 三家官方示例都是这么给的，且各自绑上自家专有语法）。**B 与 C 的真实差别不是"跟不跟业界主流"，而是那段 RRF 代码写在 `.py` 里还是写在 `.sql` 里。**

---

## Key Findings

1. **10 个系统中 8 个提供引擎内建融合。** Elasticsearch（`rrf` retriever，8.8 TP 2023-05 → 8.16 GA 2024-11；加权标注 `ga 9.2`）、OpenSearch（`score-ranker-processor` technique=rrf，hybrid 2.11 / RRF 处理器 **2.19**）、Milvus（`hybrid_search` + `RRFRanker`/`WeightedRanker`，2.4）、Weaviate（`hybrid` + `fusion_type`，relativeScoreFusion 1.20 引入 / 1.24 起默认）、Qdrant（Query API `prefetch` + `fusion`，1.10；k 参数 1.16；**加权 1.17**）、Azure AI Search（自动 RRF，无显式参数）、Vespa（`reciprocal_rank_fusion()`，限 `global-phase`）、MongoDB Atlas（`$rankFusion`/`$scoreFusion`，需 MongoDB 8.0+）。
   - 来源：见文末 Sources 1–8。

2. **pgvector 官方不提供任何融合能力。** README「Hybrid Search」节全文只给出与 Postgres 全文检索联合查询的示例，并加一句 "You can use Reciprocal Rank Fusion or a cross-encoder to combine results"。**未提及也不推荐 ParadeDB / VectorChord / pg_search**——"pgvector 官方推荐 ParadeDB"这一说法在官方 README 与 wiki 中查无实据。
   - 来源：Sources 9。

3. **ParadeDB `pg_search` 当前没有 native hybrid、没有内建 RRF。** 官方 README 路线清单仍列 `- [ ] Native Hybrid Search (coming soon)`，向量索引仍借用 pgvector。`|||` 是 BM25 的 **match disjunction** 算子（不是融合算子）。其官方博客给的"hybrid"做法是手写三段 CTE + `ROW_NUMBER()` + `1.0/(60+r)` + 手写权重。
   - 来源：Sources 10、11。

4. **Elasticsearch 原生 RRF 不支持每支路权重。** 官方原句："Each child retriever carries an equal weight as part of the RRF formula." 可调的是 `rank_constant` / `rank_window_size`（每请求参数）。
   - 来源：Sources 12。

5. **多个引擎的融合语义有"意外"或缺失。** Weaviate：BM25 侧与最终融合分**都没有阈值参数**（因为 BM25 分数未归一化、无界），且 `alpha` 的实际权重可能与 0.75 不符；Azure：RRF 的 `@search.score` 上界受融合路数约束、子分数要 `debug` 才返回；OpenSearch：归一化技术的选择**直接改变排名**，官方不下结论让你自查；Doris 官方写明 "RRF scores are not probabilities and have no absolute meaning across queries."
   - 来源：Sources 13–17。

6. **框架核心一律应用层，但下推存在于 store 集成。** LangChain `EnsembleRetriever`（`c=60`，`weighted_reciprocal_rank` 内 `rrf_score += weight/(rank+c)`）与 LlamaIndex `fusion_retriever`（`RECIPROCAL_RANK`/`RELATIVE_SCORE`/`DIST_BASED_SCORE`，`k=60`）都在 Python 内计算；Haystack `DocumentJoiner` 的 `_rrf` 用 `k=61`。**下推的反例是 LlamaIndex 的 Milvus 集成**（生成 `RRFRanker`/`WeightedRanker` 交给引擎）**与 LangChain 的 MongoDB Atlas 集成**（`$rankFusion` 进聚合管道）。
   - 来源：Sources 18–21。

7. **存在"引擎有原生能力却选择不下推"的两个一手实例。** ① Haystack 的 Elasticsearch/OpenSearch hybrid retriever：两个独立查询 + `DocumentJoiner`，docstring 只说 "combining the results through a document joiner"，未给原因。② 本仓参考项目 **WeKnora**：有 OpenSearch 后端，融合仍写在 Go（见下文本地实测）。
   - 来源：Sources 22、23；本仓参考项目实测。

8. **RAGFlow 按引擎分流，是"能下推就下推、不能就应用层"的实证。** `rag/nlp/search.py:37` 构造 `FusionExpr("weighted_sum", topn, {"weights": "term_w,vec_w"})`；Infinity 路径由引擎执行（`rag/utils/infinity_conn.py:313`）；**ES 路径不实现 weighted_sum**，改为把文本查询与 `knn` 合成单条带 boost 的查询，再用 `rerank_with_knn`（`search.py:604`）在应用层重算分数。
   - 来源：Sources 24–26。

9. **Dify 全在应用层，连关键词打分都在 Python**（jieba 分词 + 自算 IDF/TF-IDF + 余弦，`api/core/rag/rerank/weight_rerank.py`）。**Onyx(Danswer) 则下推到 Vespa**（单条 YQL + rank profile 的 `global-phase` 加权 `normalize_linear`）。
   - 来源：Sources 27、28。

10. **VLDB 2025 论文给出与"路径质量"直接相关的一手结论。** RQ3 专门问"如何有效重排来自不同检索路径的候选"，结论含 **weakest-link 现象**："a single underperforming path can disproportionately degrade overall performance... highlighting the need for **path-wise quality assessment before fusion**"；并指出 cross-encoder 在库内不实用，因此推荐**级联**：库内快速融合（RRF/WS）先取候选，再在外部用 cross-encoder 重排。
    - 来源：Sources 29。

---

## Detailed Analysis

### 主题一：为什么"引擎主流"不等于"产品主流"

把两条线的证据并排放，模式很清楚：

| 侧 | 主流做法 | 证据 |
|---|---|---|
| 引擎/数据库厂商 | 提供并**推荐**库内融合 | 8/10 有原生；所有引擎官方文档一致主张库内；**无一篇官方建议放应用层** |
| RAG 应用/产品 | **按能力分流**：有原生就下推，缺能力或需自定义就应用层 | LlamaIndex-Milvus / LangChain-MongoDB 下推；Dify 纯应用层；RAGFlow 按引擎分流；Onyx 下推 Vespa；**Haystack-ES/OS 与 WeKnora 有原生却不用** |

**关键不是"哪层更好"，而是"引擎给不给得起你要的语义"。** 需要**加权**、需要**运行时按租户可配**、需要**跨后端一致**的产品，会绕开原生能力自己算——WeKnora 的配置定义把这一点显式化了：

```
internal/types/retrieval_config.go:31   RRFK             int      // 平滑常数
internal/types/retrieval_config.go:35   RRFVectorWeight  float64  // 默认 0.5
internal/types/retrieval_config.go:37   RRFKeywordWeight float64  // 默认 0.3
```

**per-tenant 可配的加权 RRF** —— 这类需求原生能力（尤其 ES 的"权重必须相等"）给不了。

### 主题二：Postgres 是这次核查里的"能力洼地"

```
有原生融合：  ES 8.16+ · OpenSearch 2.19+ · Milvus 2.4+ · Weaviate 1.20+ ·
             Qdrant 1.10+(加权 1.17) · Azure AI Search · Vespa · MongoDB 8.0+

无原生融合：  pgvector（官方只给 "You can use RRF"）
             ParadeDB pg_search 0.25.9 仍标 Native Hybrid Search coming soon
```

因此"用 PG + 还要下推融合"的落地形态只能是自己写 SQL。三家官方示例印证（都附出处）：

- **ParadeDB**（三段 CTE + `ROW_NUMBER()` + `1.0/(60+r)`）——并特别强调**必须加 tiebreaker**：
  > "Without a tiebreaker, ... the same query can return different candidates from one run to the next — and the fused result changes with them."
- **Apache Doris**："Doris does not ship a built-in `rrf()` function, but you can implement Reciprocal Rank Fusion in plain SQL."
- **Azure HorizonDB**：同样给出 `COALESCE(v.vec_rank, 1000)` + `1.0/(60+...)` 的 SQL 写法。

且这三条 SQL 都绑上各自专有语法（ParadeDB 的 `|||`/`@@@`/`pdb.score()`/`pdb.all()`、Doris 的 `MATCH_ANY`/`score()`、HorizonDB 的 `azure_ai`）。**换引擎即不可用。**

### 主题三：本地参考项目的实测（本节为本次独立核实，非网络来源）

| 项目 | 存储 | 融合在哪 | 融合形态 | 证据 |
|---|---|---|---|---|
| **financial_rag-main**（同域，**pgvector + tsvector**，与本项目目标栈一致）| PG + pgvector + tsvector | **Python** | **按 domain 加权的 RRF**，输出带 `dense_rank`/`sparse_rank` | `rag_backend/app/services/hybrid_search.py:45-83` → `:87-125` `_rrf_fusion` |
| **WeKnora**（有 OpenSearch 后端）| OpenSearch / 其它 | **Go** | **per-tenant 可配加权 RRF** | `internal/application/service/knowledgebase_search_fusion.go:84-125` |
| **RAGFlow** | ES / Infinity | **按引擎分流** | Infinity：引擎 `weighted_sum`；ES：单查询 boost + 应用层重排 | `rag/nlp/search.py:37,326,329`；`rag/utils/infinity_conn.py:313`；`rag/utils/es_conn.py:236-243` |
| **Dify** | 多种 | **Python** | 加权分数（`weighted_score`），关键词分也在 Python 算 | `api/core/rag/rerank/weight_rerank.py:60` |

两个最值得注意的点：

1. **`financial_rag-main` 证明"分路可辨"不需要下推。** 它在应用层输出里同时带 `dense_rank` 与 `sparse_rank` —— 这只要求两路查询各自返回名次，与融合在哪一层无关。所以"一次往返 + 分路分数"不是下推的特权。
2. **`financial_rag-main:62-71` 给 `asyncio.gather` 加了 `return_exceptions=True`**，把两路失败都降级成空（`:73-76` 只记 error）。这与本项目「dense 是主路、dense 失败就该失败」的决定**相反** —— 该反例支持本项目现有的不对称降级决定。

---

## Contrarian Views And Risks

**把融合写进 SQL / 引擎 pipeline 的代价（全部附出处）**

1. **SQL 复杂度与确定性**：需手写多段 CTE + 窗口函数；ParadeDB 官方明确要求加 tiebreaker，否则**同一条查询两次运行可能返回不同候选**（详见主题二）。
2. **被特定扩展锁定**：ParadeDB 的 RRF SQL 依赖 `|||`/`@@@`/`pdb.score()`；Doris 依赖 `MATCH_ANY`/`score()`；HorizonDB 要求用 `azure_ai` 生成 embedding。
3. **权重能力受限**：Elasticsearch 原生 RRF "各 child retriever 权重必须相等"。
4. **阈值/分数语义被改变**：Azure 的 `@search.score` 上界受融合路数约束、子分数需 `debug`；Weaviate 的 BM25 侧与最终分**都没有阈值**；Doris 官方："RRF scores are not probabilities and have no absolute meaning across queries."
5. **性能并非免费**：Azure HorizonDB "Hybrid search adds two index scans and a fusion step. It's not free."；Doris 提示 hot-path latency；VLDB 2025 论文指出每增加一条检索路径会显著抬高延迟与内存。
6. **观测需要引擎专有工具**：ParadeDB 要 `EXPLAIN (COSTS OFF)` 看到 `TopKScanExecState` 才算下推生效；Azure 要 `debug` 拿子分数；ES 要看 `_rank`（`_score` 已被 RRF 覆盖）。

**明确标注为未确认 / 推断的部分（不要当成结论用）**

- **"改公式要重启服务"**：未确认。一手材料显示改公式 = 改 SQL（ParadeDB/Doris/HorizonDB）或改 pipeline 配置（OpenSearch）或改请求参数（ES），均未提重启。
- **"库内融合更难单测"**：**推断**，无一手来源直接讨论。（这也是本次提问被要求排除的因素。）
- **"库内融合无法跨存储"**：**推断**。核查到的全部库内实现都只在**同一引擎内**融合，没有任何官方文档描述跨存储融合；但也没有任何来源给出"因此应用层更好"的结论。
- **"应用层更好 / 库内更好"**：**所有来源均未给出**。本次调研也不下此结论。
- Elasticsearch 的 **RRF 是否属付费档**：仅有二手来源一致指向 Basic 返回 403，未取到 Elastic 官方 subscriptions 页原文逐条确认。
- Azure AI Search 与 Vespa 的 RRF **引入版本**：官方页面未标注，未找到权威出处。
- **不存在任何一篇权威来源系统对比"应用层融合 vs 库内融合"并给结论**；取舍目前只能由上面的分项代价拼出。

---

## Open Questions

1. **本项目要用原生融合，唯一的托管路径是否是"换引擎"？** 本次核查显示 PG 生态没有可下推的融合。若"融合由引擎负责"是硬需求，则 Milvus（`WeightedRanker`/`RRFRanker`）或 OpenSearch 2.19+ 才有；这会把选型从"收敛到 PG"推向"PG + 另一个检索引擎"。**未验证**：阿里云托管的 Milvus 版与托管 OpenSearch 是否都在可用清单内。
2. **中文分词与融合落点是否正交？** 本次未研究"引擎原生 RRF 对中文分词的支持差异"（如 ES/OpenSearch 的 `zhparser` 类分析器 vs PG 的 `zhparser`/`pg_bigm`）。二者可能相互牵制：选了引擎原生融合，就同时锁定了该引擎的中文分析器。**未确认**。
3. **`weakest-link` 现象在本项目的量化形态？** VLDB 2025 指出单条弱路径会不成比例地拉低整体。本项目已有 `retrieval-fetch-and-dedup` 想做的分路计数观测，但**尚未实测** BM25 路径变弱时融合结果的退化幅度。
4. **tiebreaker 的等价要求**：若最终选 B（SQL 内融合），必须复现现有 `rrf_fusion`（`src/infra/search/bm25_index.py:120-152`）的确定性顺序，包括同分时的稳定排序。当前 Python 实现依赖 `sorted` 的稳定性与 dict 插入序，**SQL 侧需要显式 tiebreaker**（ParadeDB 官方警告），二者是否逐条等价**未验证**。

---

## Sources

**引擎原生融合能力**

1. Elasticsearch `rrf` retriever（官方文档，含 `rank_constant`/`rank_window_size`）— https://github.com/elastic/elasticsearch/blob/main/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever.md
2. Elasticsearch 8.8 release highlights（RRF 引入，协调节点融合原句）— https://www.elastic.co/guide/en/elasticsearch/reference/8.8/release-highlights.html
3. Elasticsearch 8.16 新特性（retrievers + RRF GA）— https://www.elastic.co/blog/whats-new-elastic-search-8-16-0
4. OpenSearch `score-ranker-processor`（RRF，`rank_constant` 默认 60）— https://raw.githubusercontent.com/opensearch-project/documentation-website/main/_search-plugins/search-pipelines/score-ranker-processor.md
5. OpenSearch hybrid search 概览（Introduced 2.11）— https://raw.githubusercontent.com/opensearch-project/documentation-website/main/_vector-search/ai-search/hybrid-search/index.md
6. OpenSearch 官方博客：Introducing RRF for hybrid search（2.19）— https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/
7. Milvus `hybrid_search` API（`WeightedRanker`/`RRFRanker`）— https://milvus.io/api-reference/pymilvus/v2.5.x/MilvusClient/Vector/hybrid_search.md
8. Milvus 2.4 multi-vector search（hybrid 框架引入）— https://milvus.io/docs/v2.4.x/multi-vector-search.md
9. Weaviate hybrid search 概念（两种 fusion 算法）— https://docs.weaviate.io/weaviate/concepts/search/hybrid-search
10. Weaviate hybrid 查询与 `alpha` 说明 — https://docs.weaviate.io/weaviate/search/hybrid
11. Weaviate 官方博客：fusion 算法对比 — https://weaviate.io/blog/hybrid-search-fusion-algorithms
12. Qdrant hybrid queries（`prefetch` + `fusion`，含加权 RRF 版本门槛）— https://qdrant.tech/documentation/search/hybrid-queries/
13. Azure AI Search hybrid ranking（自动 RRF、`@search.score` 语义）— https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking
14. Vespa hybrid search（`reciprocal_rank_fusion()` 限 `global-phase`）— https://learn.vespa.ai/vector-search/hybrid-search/
15. MongoDB Atlas `$rankFusion`（MongoDB 8.0+）— https://www.mongodb.com/docs/manual/reference/operator/aggregation/rankfusion/
16. MongoDB 官方公告：`$rankFusion`/`$scoreFusion` GA — https://www.mongodb.com/products/updates/now-ga-hybrid-search-with-rankfusion-and-scorefusion/

**Postgres 生态**

17. pgvector README「Hybrid Search」节（官方唯一 hybrid 表述：You can use RRF）— https://github.com/pgvector/pgvector
18. ParadeDB `pg_search` README（Native Hybrid Search 仍 coming soon；版本 0.25.9）— https://pgxn.org/dist/pg_search/
19. ParadeDB 官方博客：Hybrid Search in PostgreSQL: The Missing Manual（手写 CTE 方案）— https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual
20. ParadeDB 官方文档：RRF 参考实现（**tiebreaker 警告原文**）— https://www.paradedb.com/docs/reference/hybrid/rrf
21. Apache Doris：Reciprocal Rank Fusion（"不提供内建 rrf()，用纯 SQL 实现"）— https://doris.apache.org/docs/4.x/key-features/reciprocal-rank-fusion/
22. Azure HorizonDB：hybrid search（SQL 内融合 + "not free" 原文）— https://learn.microsoft.com/en-us/azure/horizondb/ai/hybrid-search

**框架层**

23. LangChain `EnsembleRetriever`（`weighted_reciprocal_rank`，`c=60`）— https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/retrievers/ensemble.py
24. LlamaIndex `fusion_retriever.py`（三种 fusion 模式，`k=60`）— https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/retrievers/fusion_retriever.py
25. Haystack `DocumentJoiner`（`reciprocal_rank_fusion`）— https://github.com/deepset-ai/haystack/blob/main/haystack/components/joiners/document_joiner.py
26. Haystack `_reciprocal_rank_fusion`（`k=61`）— https://github.com/deepset-ai/haystack/blob/main/haystack/utils/misc.py
27. Haystack Elasticsearch hybrid retriever（**有原生 RRF 却走 DocumentJoiner**）— https://github.com/deepset-ai/haystack-core-integrations/blob/main/integrations/elasticsearch/src/haystack_integrations/components/retrievers/elasticsearch/elasticsearch_hybrid_retriever.py
28. LangChain MongoDB Atlas hybrid retriever（**下推 `$rankFusion`**）— https://github.com/langchain-ai/langchain-mongodb/blob/main/libs/langchain-mongodb/langchain_mongodb/retrievers/hybrid_search.py

**平台与学术**

29. RAGFlow `rag/nlp/search.py`（`build_fusion_expr` 按引擎分流）— https://github.com/infiniflow/ragflow/blob/main/rag/nlp/search.py
30. RAGFlow `rag/utils/infinity_conn.py`（Infinity 执行 `weighted_sum`）— https://github.com/infiniflow/ragflow/blob/main/rag/utils/infinity_conn.py
31. RAGFlow `rag/utils/es_conn.py`（ES 路径改为 boost，不做 weighted_sum）— https://github.com/infiniflow/ragflow/blob/main/rag/utils/es_conn.py
32. Dify `weight_rerank.py`（加权分数，关键词分在 Python 现算）— https://github.com/langgenius/dify/blob/main/api/core/rag/rerank/weight_rerank.py
33. Onyx(Danswer) Vespa 索引（hybrid 单条 YQL + rank profile 融合）— https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/document_index/vespa/vespa_document_index.py
34. Onyx Vespa schema（`global-phase` 加权 `normalize_linear`）— https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/document_index/vespa/app_config/schemas/danswer_chunk.sd.jinja
35. VLDB 2025《Balancing the Blend》（weakest-link 与级联重排结论）— https://arxiv.org/html/2508.01405v1 ／ https://dl.acm.org/doi/abs/10.14778/3811243.3811246

**本地参考项目（本次独立核实，非网络来源，路径为 `/mnt/d/code/demo/AIAgent/github/`）**

36. `financial_rag-main/rag_backend/app/services/hybrid_search.py:45-125`（pgvector+tsvector 栈，应用层加权 RRF，带 `dense_rank`/`sparse_rank`）
37. `WeKnora/internal/application/service/knowledgebase_search_fusion.go:84-125` + `internal/types/retrieval_config.go:31,35,37`（OpenSearch 后端却应用层加权 RRF，per-tenant 可配）
38. `ragflow-0.26.4/rag/nlp/query.py:168`（`hybrid_similarity(tkweight=0.3, vtweight=0.7)`）
39. `dify-1.16.1/api/core/rag/rerank/weight_rerank.py:60` 与 `rerank_type.py:6`（`weighted_score`）

---

## Rerun Inputs

```
workflow: firecrawl-deep-research
topic: hybrid search 的融合放在引擎内还是应用层（含 Postgres/pgvector 的能力边界）
depth: thorough
output: markdown
```
