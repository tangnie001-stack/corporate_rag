## 1. 观测补全（先行，不改行为）

- [ ] 1.1 `src/rag/retrieval.py:191-196` `rerank done` 增 `score_max` / `score_min` / `score_p50`（由 `reranked` 的 `relevance_score` 计算；当前该事件只记 `doc_count` + `query_len`）与分数来源标记 `scored`：真实精排落 `scored=rerank`，`except` 降级分支（回退 `1 - distance`）落 `scored=fallback`。**不记 `score_top1`**（降序下恒等于 `score_max`）
- [ ] 1.2 新增 `[retrieval] dedup done` 事件，记录 `dropped` / `kept`。**约束：不改 `retrieval.search` 的返回契约、不把统计挂到 `retrieve done`**（理由见 design D4）
- [ ] 1.3 `src/core/log_events.py` / `log_event_specs.py` 登记：`rerank done` 的新字段 + `dedup done` 这个新事件
- [ ] 1.4 `docs/agents/logging-rules.md` 按开放登记制登记上述字段与事件（值类型、含义、级别）
- [ ] 1.5 单测：`tests/core/` 断言 `rerank done` / `dedup done` 的字段齐全；**降级路径（rerank 抛异常）落 `scored=fallback`、正常路径落 `scored=rerank`**；空输入（`rerank skip`）时不产生 `rerank done`；`retrieval.search` 的返回类型不变
- [ ] 1.6 实跑一轮真实请求，确认字段可读（`rerank done` 的 `score_*` + `scored` + `dedup done`）

> `hybrid done` 的分路计数（`dense_count` / `sparse_count`）**不在本变更范围** —— 在效 spec「稀疏支路贡献可见」已要求、代码已实现（`src/rag/retrieval.py`），本变更不重复要求、不改字段名。

## 2. 候选池默认值

- [ ] 2.1 `src/config/settings.py:243` 默认值 50 → 30（**已先行落地，本任务仅勾选确认**）
- [ ] 2.2 `.env` / `README.md:283` 同步为 30（**已先行落地，本任务仅勾选确认**）
- [ ] 2.3 确认 `retrieval.search` 的**非 hybrid 分支**（`retrieval.py:105-117`）在当前配置（`HYBRID_SEARCH_ENABLED=true`）下生产不可达，无需额外处理（在 design 里记为已知事实）

## 3. 去重单位与位置切换（核心）

- [ ] 3.1 先写会失败的测试（`tests/rag/test_retrieval.py` / `test_retrieval_dedup.py`）四组断言：① 同一文档多个不同父块全部保留；② 同一父块多个 chunk 只留一条；③ **保留的是精排分最高的那条**（构造同父块两条、分数一高一低，断言留下高分那条）；④ **降级路径保留 `1 - distance` 最高的那条**。现有断言写的是 doc_id 语义与"rerank 前"，属契约变更，需一并改写
- [ ] 3.2 `src/rag/retrieval.py`：`search` **不再去重**（去掉 `:102` hybrid / `:116` 非 hybrid 两处 `_dedup_by_doc_id` 调用）；去重**移到精排之后**、`rerank_results` 内对全部候选打分之后执行。⚠ `rerank_results` 须**先对全部 `reranked` 候选建 `RAGContext`（含 `.score`）→ 再去重 → 最后 `[:TOP_K_RERANK]`**；若仍按现状先截断再建 context，`dedup done` 的 `dropped`（精排后条数 − kept）算不出来
- [ ] 3.3 去重键：**`(doc_id, hash(parent_content))`**；签名 `_dedup_by_parent(contexts: list[RAGContext]) -> list[RAGContext]`，每键保留 **`.score` 最高**的一条（`RAGContext` 无 `distance` 字段，故「内部自取分」在类型上即不可行）；`parent_content` 缺失时按该 chunk 自身保留（不参与折叠）。⚠ 必须把 `doc_id` 纳入键（跨文档样板文本会误折叠）；不要用 `heading_path`（实测 `(doc_id, heading_path)` 在 11/51 组里对应多个父块）；不要用全文做 dict key
- [ ] 3.4 截断顺序改为 **先打分 → 再去重 → 再 `[:TOP_K_RERANK]`**（当前 `retrieval.py:164` 在没有去重的前提下直接 `reranked[:TOP_K_RERANK]`，须把截断移到去重之后）。函数改名 `_dedup_by_parent` 并更新 docstring（当前名 `_dedup_by_doc_id` 与新区义不符）
- [ ] 3.5 **删除 `RETRIEVAL_MAX_PER_DOC`**（`src/config/settings.py`）—— 不留失效旋钮。连带清理（漏一处即 AttributeError 或契约漂移，完整清单）：
  - `[retrieval] retrieve replay` 事件的 `dedup_max_per_doc` 字段（写方 `src/agents/tools/rag_tools.py`；容器 `src/core/log_events.py` 的 `ReplayEvent.dedup_max_per_doc`；字段登记 `src/core/log_event_specs.py`）
  - `src/cli/replay_trace.py`：`settings.RETRIEVAL_MAX_PER_DOC` 读取、drift 对照的 `dedup` 轴、params 打印，以及**模块顶部 docstring 与 `_print_snippets` docstring 里的 `dedup` 字样**（当前仍写 `top_k/dedup/hybrid/rerank`）
  - 测试：`tests/cli/test_replay_trace.py`、`tests/core/test_log_events.py`
  - delta spec：本变更已在 `specs/observability-logging/spec.md` 的 MODIFIED 中把该字段从「检索重放上下文日志」与「离线重放 CLI」两条要求移除
- [ ] 3.6 全量跑 `pytest tests/ -v` + `ruff check .` + `pyright src/`，确认无回归
- [ ] 3.7 实跑（KB 均已存在，核实于 2026-09-24）：
  - **`4a1dcb8b`（1 文档 / 38 chunk / 9 父块 —— 即 trace_19e8e472 的 KB）**：期望 `retrieve done result_count` 从 **1 升到 5**（= `TOP_K_RERANK`，精排窗口被填满）
  - `b9e74e82`（2 文档 / 51 chunk / 12 父块）：期望不再恒为 2
  - `ea84fb72`（3 文档 / 121 chunk / 30 父块）：单文档 85 chunk 的"占满窗口"最强样本（见 5.2）；⚠ 其 `parent_content` 覆盖率仅 52/121≈43%（三库最低），去重效力最弱，结论须把该因素与"单文档占满窗口"分开解释
  - 三例均核对 `dedup done` 的 `dropped` / `kept` 反映真实折叠量
- [ ] 3.8 **降级路径同样去重**：`src/agents/tools/rag_tools.py` 的 `except TimeoutError` 分支（约 `:144-176`）绕过 `rerank_results`、按检索原始顺序手工构造 `RAGContext`。改后 `search` 不再去重，该路径会重新引入"同一父块被重复渲染"——须让去重步骤在**两条路径**上复用（降级路径同样调用 `_dedup_by_parent`、代表分传 `1 - distance`，并落 `dedup done`）

## 4. 失效资产处置

- [ ] 4.1 **作废 `src/cli/compare_dedup.py`**（口径变更后无可调参数，无 A/B 可言），并在 `docs/agents/glossary.md` 注明。三处具体失义点：① 实验轴名/表头/描述仍是 `max_per_doc`（`compare_dedup.py:24,75,88-100`）；② 它靠子进程覆盖 `RETRIEVAL_MAX_PER_DOC`（`:41-43`）而该配置项将被删除；③ 结论解读失效——它评的链路已改口径，跑出来的是另一件事。**不可只留 TODO**（会误导后续实验）
- [ ] 4.2 `docs/agents/glossary.md:40-41` 术语改名：**删掉 `dedup` 与 `RETRIEVAL_MAX_PER_DOC` 两个词条，新增「父块级去重」**（定义按现状写"同一文档内同一父块至多保留 1 条"，不写"原来是 doc_id"这类历史）。理由：`dedup` 一名会同时指向前后两种口径，读者必须靠历史说明才能读懂，违反 glossary 的"只描述现状"规约；且"去重"在本项目有三个不同层次（引用展示去重 / RRF 候选去重 / 父块级去重），各需精确词条
- [ ] 4.3 复核 `tests/rag/test_prompt_layers.py` 等测试是否隐含依赖"每文档 1 条"的结果条数
- [ ] 4.4 改名带来的悬空引用一并清理：① `src/cli/eval_ragas.py` 中引用已作废 `compare_dedup` 的注释；② `docs/agents/defensive-patterns.md` 以 `_dedup_by_doc_id` 为例的段落（改名 `_dedup_by_parent` 后须同步）

## 5. 分数形态采样与判据结论

本组**独立于 §1/§2/§3/§6**：它只读观测数据、不改检索行为，可在前述各节落地后单独进行（也可按需拆分为独立变更）。先前写的"前置：等 change `bm25-index-durability` 落地"已删除 —— 该 change 不存在，且词法路现由 PostgreSQL 全文检索承担（`sparse_count` 非零，见 `hybrid done`），无静默失效前提。

- [ ] 5.1 采样脚本：跨 2 个 KB（`b9e74e82`、`ea84fb72`）× ≥ 10 个 query，记录 `score_max` / 分布形状 / 库内实际是否有对应内容。**只统计 `scored=rerank` 的样本**（`scored=fallback` 的 `1 - distance` 分不参与形态判定与阈值校准）
- [ ] 5.2 `ea84fb72`（121 chunk / 3 文档 / 单文档 85 chunk）必须纳入 —— 它是"单文档占满窗口"风险的最强样本。⚠ 但其 `parent_content` 覆盖率仅 52/121≈43%，去重效力被稀释；采样结论须把"低覆盖率"与"单文档占满窗口"分开解释
- [ ] 5.3 输出结论文档**落到 `docs/agents/` 下一个登记过的归属文档**（不用 `docs/tmp/` —— 该结论会被后续变更长期引用）：双形态假说是否稳定、top1 的分界区间落在哪、是否需要第二个特征（断层幅度）
- [ ] 5.4 明确写清**本变更不接控制流**：拦截层与动作留待后续变更决策
- [ ] 5.5 记录采样成本（调用次数 × 精排输入条数），供后续同类实验估算：单次采样 = `query 数 × 精排调用数（每 query 1 次，30 条输入）`，两个 KB 合计约 `2 × N_query` 次精排调用

## 6. spec 存量 drift 修正与归档

- [ ] 6.1 `docs/openspec/specs/retrieval-quality/spec.md:7` 默认值 10/5 → 30/5（与代码对齐）
- [ ] 6.2 同文件 `:33-34` 评测矩阵 `TOP_K_RETRIEVAL: 5, 10, 15` → `10, 30, 50`；**同步改实现与说明**，否则 spec 又与它描述的 CLI 不符（同一类 drift）：`src/cli/compare_retrieval.py`（`RETRIEVAL_VALUES = [5, 10, 15]`）与 `src/cli/README.md`（网格描述 `[5, 10, 15] × [3, 5, 8]`）
- [ ] 6.3 同文件 `:66-68` 移除"语义选库检索"scenario（`_semantic_select_kb` 已废弃，`rag_tools.py:131` 注释在案）
- [ ] 6.4 归档前校验：`openspec validate retrieval-fetch-and-dedup` 通过
- [ ] 6.5 归档本变更，delta 合并进在效 spec

## 7. 收尾

- [ ] 7.1 `docs/adr/0001-retrieval-fetch-and-dedup-scope.md` 的 Status 由 `Accepted` 复核为已落地（若有偏差需补记）
- [ ] 7.2 提交信息里写明：本次推翻了 `retrieval-quality`「检索去重策略参数化」与 `retrieval-judgment`「检索结果去重」两条要求的口径

## 8. 完成定义（DoD）

以下为**可证伪**的验收判据；全部满足方视为达成目标。此前 tasks 只有动作、无判据（评审 F3）。

- [ ] 8.1 **单文档库不再被压成 1 条**：重放 trace `trace_19e8e472` 的 4 个 query（KB `4a1dcb8b`），iteration-1 的 `retrieve done result_count` ≥ 3（期望 5 = `TOP_K_RERANK`），且不再出现 `rerank done doc_count=1`
- [ ] 8.2 **循环不再触顶**：同 session 重问"能帮忙查一下腾讯2024年第四季度业绩情况吗"，答案在 `iteration < 5` 产出，日志**不出现** `[agent] iteration limit`
- [ ] 8.3 **多文档库不再被文档数压顶**：`b9e74e82`（2 文档 / 12 父块）上 `retrieve done result_count` 不再恒为 2
- [ ] 8.4 **天花板可见且口径唯一**：`dedup done` 的 `kept` = **去重后条数（截断前）**，`dropped` = `精排后条数 − kept`；最终 `retrieve done` 的 `result_count` = **`min(kept, TOP_K_RERANK, top_k)`**（去重与 `TOP_K_RERANK` 截断都在 `rerank_results` 内，`rag_tools` 再截 `top_k`），故恒有 `result_count ≤ kept`（二者**不要求相等**）。注：`top_k > TOP_K_RERANK` 时 `top_k` 不生效（`rerank_results` 硬截 `TOP_K_RERANK`），属**存量问题**，本变更不修；`dedup_max_per_doc` 字段不再出现在任何 `retrieve replay` 行
- [ ] 8.5 **契约不破**：`pytest tests/ -v` 全绿；`retrieval.search` 返回类型仍为 `list`；`replay_trace` 在**含 `dedup_max_per_doc` 的历史日志行**上仍能解析、不抛错（该字段只被忽略，不被读取）
- [ ] 8.6 **边界声明**：循环端的提前止损（`retrieval_exhausted`）**不在本变更 DoD 内**。若 8.2 未达成，须先判定是"材料仍不足"还是"循环信号缺失"；后者归后续变更（`docs/superpowers/specs/2026-09-16-retrieval-exhaustion-early-stop-design.md`）
- [ ] 8.7 **交叉注记**：`docs/superpowers/specs/2026-09-16-retrieval-exhaustion-early-stop-design.md`（多处以 `_dedup_by_doc_id` / `RETRIEVAL_MAX_PER_DOC` 为前提）与 `docs/superpowers/plans/2026-09-21-prompt-layering-p1-sections.md:25`（以「`RETRIEVAL_MAX_PER_DOC` 仍在」作为 change 未落地的证据）须补一条"该前提已由本变更改写"的交叉注记，避免读者误读
