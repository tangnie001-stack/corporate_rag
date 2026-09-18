## 1. 观测补全（先行，不改行为）

- [ ] 1.1 `src/rag/retrieval.py:89-94` `hybrid done` 增 `dense_count` / `bm25_count`（`len(d)` / `len(b)`，两路各自原始条数）
- [ ] 1.2 `src/rag/retrieval.py:191-196` `rerank done` 增 `score_top1` / `score_max` / `score_min` / `score_p50`（由 `reranked` 的 `relevance_score` 计算；当前该事件只记 `doc_count` + `query_len`）
- [ ] 1.3 新增 `[retrieval] dedup done` 事件，记录 `dropped` / `kept`。**约束：不改 `retrieval.search` 的返回契约、不把统计挂到 `retrieve done`**（理由见 design D4）
- [ ] 1.4 `src/core/log_events.py` / `log_event_specs.py` 登记：`hybrid done` / `rerank done` 的新字段 + `dedup done` 这个新事件
- [ ] 1.5 `docs/agents/logging-rules.md` 按开放登记制登记上述字段与事件（值类型、含义、级别）
- [ ] 1.6 单测：`tests/core/` 断言 `hybrid done` / `rerank done` / `dedup done` 的字段齐全；空输入（`rerank skip`）时不产生 `rerank done`；`retrieval.search` 的返回类型不变
- [ ] 1.7 实跑一轮真实请求，确认字段可读（含 `bm25_count=0` 的失效样本）

## 2. 候选池默认值

- [ ] 2.1 `src/config/settings.py:179` 默认值 50 → 30（**已先行落地，本任务仅勾选确认**）
- [ ] 2.2 `.env` / `README.md:283` 同步为 30（**已先行落地，本任务仅勾选确认**）
- [ ] 2.3 确认 `src/infra/db/vector_store/search.py:76` 的 `similarity_search_all(k=TOP_K_RETRIEVAL)` 属生产不可达路径，无需额外处理（在 design 里记为已知事实）

## 3. 去重单位切换（核心）

- [ ] 3.1 先写会失败的测试：`tests/rag/test_retrieval.py` 新增"同文档多父块全部保留"与"同父块多 chunk 只留一条"两组断言（现有断言写的是 doc_id 语义，属契约变更，需一并改写）
- [ ] 3.2 `src/rag/retrieval.py:33-62` `_dedup_by_doc_id` 的去重键由 `metadata["doc_id"]` 改为父块标识；每父块保留 1 条
- [ ] 3.3 父块标识取法：键为 **`(doc_id, hash(parent_content))`**；`parent_content` 缺失时按该 chunk 自身保留（不去重）。⚠ 必须把 `doc_id` 纳入键（跨文档样板文本会误折叠）；不要用 `heading_path`（实测 `(doc_id, heading_path)` 在 11/51 组里对应多个父块）；不要用全文做 dict key
- [ ] 3.4 函数改名 `_dedup_by_parent` 并更新 docstring（当前名 `_dedup_by_doc_id` 与新区义不符）；同步更新 `retrieval.py:95` / `:116` 两处调用点
- [ ] 3.5 **删除 `RETRIEVAL_MAX_PER_DOC`**（`src/config/settings.py:183`）—— 不留失效旋钮；同步移除 `[retrieval] retrieve replay` 事件的 `dedup_max_per_doc` 字段（该字段会输出一个已无意义的值，污染重放对照）
- [ ] 3.6 全量跑 `pytest tests/ -v` + `ruff check .` + `pyright src/`，确认无回归
- [ ] 3.7 用 `b9e74e82`（2 文档 / 51 chunk / 14 父块）实跑，确认 `retrieve done` 的 `result_count` 不再恒为 2，且 `dedup done` 的 `dropped` 反映真实丢弃量

## 4. 失效资产处置

- [ ] 4.1 **作废 `src/cli/compare_dedup.py`**（口径变更后无可调参数，无 A/B 可言），并在 `docs/agents/glossary.md` 注明。三处具体失义点：① 实验轴名/表头/描述仍是 `max_per_doc`（`compare_dedup.py:24,75,88-100`）；② 它靠子进程覆盖 `RETRIEVAL_MAX_PER_DOC`（`:41-43`）而该配置项将被删除；③ 结论解读失效——它评的链路已改口径，跑出来的是另一件事。**不可只留 TODO**（会误导后续实验）
- [ ] 4.2 `docs/agents/glossary.md:36-37` 术语改名：**删掉 `dedup` 与 `RETRIEVAL_MAX_PER_DOC` 两个词条，新增「父块级去重」**（定义按现状写"同一文档内同一父块至多保留 1 条"，不写"原来是 doc_id"这类历史）。理由：`dedup` 一名会同时指向前后两种口径，读者必须靠历史说明才能读懂，违反 glossary 的"只描述现状"规约；且"去重"在本项目有三个不同层次（引用展示去重 / RRF 候选去重 / 父块级去重），各需精确词条
- [ ] 4.3 复核 `tests/rag/test_prompt_layers.py` 等测试是否隐含依赖"每文档 1 条"的结果条数

## 5. 分数形态采样与判据结论

- [ ] 5.0 **前置：等 change `bm25-index-durability` 落地。** 该变更修掉 BM25 索引静默失效（索引缺失 → 混合检索退化为纯 dense），并落"索引缺失可见"事件。**不修就采样，会把"混合已退化为纯 dense"的噪声当成信号**。若该变更被推迟，至少固定并记录 BM25 状态，并在结论文档里显式标注该前提
- [ ] 5.1 采样脚本：跨 2 个 KB（`b9e74e82`、`ea84fb72`）× ≥ 10 个 query，记录 `score_top1` / 分布形状 / 库内实际是否有对应内容
- [ ] 5.2 `ea84fb72`（121 chunk / 3 文档 / 单文档 85 chunk）必须纳入 —— 它是"单文档占满窗口"风险的最强样本
- [ ] 5.3 输出结论文档**落到 `docs/agents/` 下一个登记过的归属文档**（不用 `docs/tmp/` —— 该结论会被后续变更长期引用）：双形态假说是否稳定、top1 的分界区间落在哪、是否需要第二个特征（断层幅度）
- [ ] 5.4 明确写清**本变更不接控制流**：拦截层与动作留待后续变更决策
- [ ] 5.5 记录采样成本（调用次数 × 精排输入条数），供后续同类实验估算：单次采样 = `query 数 × 精排调用数（每 query 1 次，30 条输入）`，两个 KB 合计约 `2 × N_query` 次精排调用

## 6. spec 存量 drift 修正与归档

- [ ] 6.1 `docs/openspec/specs/retrieval-quality/spec.md:7` 默认值 10/5 → 30/5（与代码对齐）
- [ ] 6.2 同文件 `:33-34` 评测矩阵 `TOP_K_RETRIEVAL: 5, 10, 15` → `10, 30, 50`
- [ ] 6.3 同文件 `:66-68` 移除"语义选库检索"scenario（`_semantic_select_kb` 已废弃，`rag_tools.py:134` 注释在案）
- [ ] 6.4 归档前校验：`openspec validate retrieval-fetch-and-dedup` 通过
- [ ] 6.5 归档本变更，delta 合并进在效 spec

## 7. 收尾

- [ ] 7.1 `docs/adr/0001-retrieval-fetch-and-dedup-scope.md` 的 Status 由 `Accepted` 复核为已落地（若有偏差需补记）
- [ ] 7.2 提交信息里写明：本次推翻了 `retrieval-quality`「检索去重策略参数化」与 `retrieval-judgment`「检索结果去重」两条要求的口径
