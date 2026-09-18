## Why

`trace_c54ce259`（2026-09-16）表症是"KB 检索 4 次、最终 `answer_len=0`"。定位到**取数口径**有三处叠加缺陷，且它们都在 rerank 上游，精排无从补救：

1. **去重单位选错**：`RETRIEVAL_MAX_PER_DOC=1` 按 `doc_id` 限流，天花板 = 文档数（实测 2~3），且位置在 rerank **之前** —— 精排看到的候选已被提前阉割。实测（2026-09-18，`kb_b9e74e82…`，候选池 50）：4 个 query 的 `dedup@1` 结果**恒为 2**。
2. **候选池被配置缩到 8**（`settings.py` 代码默认 50，`.env` 覆盖为 8），库覆盖率仅 0.5%；千文档规模下为 0.013%。
3. **取数过程不可观测**：`HYBRID_DONE` 只记融合后条数（BM25 静默失效半个月无人发现），`RERANK_DONE` 不记分数（无法校准任何阈值），`RETRIEVE_DONE` 不记去重丢弃量（天花板不可见）。

同域对照：WeKnora `DefaultRetrievalTopK = 50`、精排后 10；业界推荐候选池 30~100、精排后 5~10。**两家均无"每文档保留 N 条"这一类约束**，WeKnora 的有效去重是"每父块 1 条"。决策理由与代价见 `docs/adr/0001-retrieval-fetch-and-dedup-scope.md`。

同时刻发现 `retrieval-quality` spec 存在 3 处存量 drift（默认值与代码不符、评测矩阵区间过期、语义选库已废弃），一并修正。其中第三处不是"与代码不符"这么轻 —— **它与另一份在效 spec 直接矛盾**：`kb-routing` spec 已明写"跨库'所有知识库'语义路由 SHALL 废弃…不再对用户查询做知识库语义匹配"，而 `retrieval-quality:66-68` 仍在要求"以语义匹配 query 与各 KB 的 name+description，选中相似度最高的 1 个知识库进行检索"。

另外，本条去重口径在**两份在效 spec 里各有 owner**（`retrieval-quality:121-123` 与 `retrieval-judgment:42-48`）—— 本变更借此把重复消解掉：口径留在 `retrieval-judgment`，`retrieval-quality` 那条改为移除。

## What Changes

- **BREAKING** 检索去重单位从**文档**（`doc_id`）改为**内容**（父块）：每父块保留 1 条；无 `parent_content` 的 chunk 按自身保留。
- 候选池 `TOP_K_RETRIEVAL` 默认值改为 **30**（已先行落地：`src/config/settings.py:179`、`.env`、`README.md:283`）。
- 补全取数观测：
  - `[retrieval] hybrid done` 增 `dense_count` / `bm25_count`
  - `[retrieval] rerank done` 增 `score_top1` / `score_max` / `score_min` / `score_p50`
  - 新增 `[retrieval] dedup done` 事件，记录 `dropped` / `kept`（**不改 `retrieval.search` 的返回契约**）
- 采样并产出"库内有无该内容"的**判据结论**（依据 `rerank` 分数的双形态：库内有 → 双峰断层；库内无 → 平滑无拐点）。本变更只产出结论，**不接控制流**。
- 修正 `retrieval-quality` spec 的 3 处存量 drift；supersede `retrieval-quality` 与 `retrieval-judgment` 中"按 doc_id 去重"的要求。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `retrieval-quality`: 候选池默认值与取值区间、`Cross-document aggregation` 与 `检索去重策略参数化` 两条互斥要求的消解、评测矩阵区间更新、废弃的"语义选库"要求移除。
- `retrieval-judgment`: `检索结果去重` 要求由"按 doc_id 去重、每文档保留最先出现的结果"改为"按内容（父块）去重"。
- `observability-logging`: `hybrid done` / `rerank done` / `retrieve done` 三个事件的字段扩展。

## Impact

**代码**

- `src/config/settings.py` — 候选池默认值（已改）
- `src/rag/retrieval.py` — `_dedup_by_doc_id` 改为内容级去重；`hybrid done` / `rerank done` 字段
- `src/agents/tools/rag_tools.py` — `retrieve done` 字段
- `src/core/log_events.py` / `log_event_specs.py` — 事件字段登记

**测试**

- `tests/rag/test_retrieval.py`（去重口径）、`tests/agents/tools/`（`retrieve done` 字段）、`tests/config/`、`tests/core/`（日志字段）

**文档**

- `docs/agents/glossary.md` — `dedup` / `RETRIEVAL_MAX_PER_DOC` 词条改写（单位变更）
- `docs/agents/logging-rules.md` — 新字段按开放登记制登记
- `README.md` — 配置表（已改）
- `docs/adr/0001-retrieval-fetch-and-dedup-scope.md` — 已写（本变更的决策记录）

**失效资产**

- `src/cli/compare_dedup.py` — A/B 网格 `{1,2,3}` 针对文档配额，单位变更后失效（改为内容级或标记作废）

**明确不在本变更范围**

- 第 3 层 BM25 静默失效与索引持久化（独立缺陷，另有清单 F1–F7）
- 判据接控制流（拦截层与动作尚未决定）
- 绝对分数阈值（"不做绝对阈值"是既有决策，见 `retrieval-judgment` spec）
- 破损表格碎片（属分块问题）
