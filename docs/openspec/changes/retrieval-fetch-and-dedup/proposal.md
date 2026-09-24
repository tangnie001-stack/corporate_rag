## Why

`trace_c54ce259`（2026-09-16）表症是"KB 检索 4 次、最终 `answer_len=0`"。定位到**取数口径**有三处叠加缺陷，且它们都在 rerank 上游，精排无从补救：

1. **去重单位选错**：`RETRIEVAL_MAX_PER_DOC=1` 按 `doc_id` 限流，天花板 = 文档数（实测 2~3），且位置在 rerank **之前** —— 精排看到的候选已被提前阉割。实测（2026-09-18，`kb_b9e74e82…`，候选池 50）：4 个 query 的 `dedup@1` 结果**恒为 2**。
2. **候选池被配置缩到 8**（`settings.py` 代码默认 50，`.env` 覆盖为 8），库覆盖率仅 0.5%；千文档规模下为 0.013%。
3. **取数过程不可观测**：`RERANK_DONE` 不记分数（无法校准任何阈值），`RETRIEVE_DONE` 不记去重丢弃量（天花板不可见）。（分路计数 `dense_count` / `sparse_count` 已在 `hybrid done` 落盘，见在效 spec「稀疏支路贡献可见」。）

触发本变更的 trace 有两条，**同一根因、不同表现**：`c54ce259`（2026-09-16，2 文档库 → 天花板恒为 2）与 `trace_19e8e472`（2026-09-23，单文档库 `4a1dcb8b` → 天花板恒为 1，agent 连查 4 轮后第 5 轮触顶 `[agent] iteration limit`）。§8 的 DoD 8.1/8.2 以后者为验证对象。

同域对照：WeKnora `DefaultRetrievalTopK = 50`、精排后 10；业界推荐候选池 30~100、精排后 5~10。**两家均无"每文档保留 N 条"这一类约束**，WeKnora 的有效去重是"每父块 1 条"。决策理由与代价见 `docs/adr/0001-retrieval-fetch-and-dedup-scope.md`。

同时刻发现 `retrieval-quality` spec 存在 3 处存量 drift（默认值与代码不符、评测矩阵区间过期、语义选库已废弃），一并修正。其中第三处不是"与代码不符"这么轻 —— **它与另一份在效 spec 直接矛盾**：`kb-routing` spec 已明写"跨库'所有知识库'语义路由 SHALL 废弃…不再对用户查询做知识库语义匹配"，而 `retrieval-quality:66-68` 仍在要求"以语义匹配 query 与各 KB 的 name+description，选中相似度最高的 1 个知识库进行检索"。

另外，本条去重口径在**两份在效 spec 里各有 owner**（`retrieval-quality:121-123` 与 `retrieval-judgment:42-48`）—— 本变更借此把重复消解掉：口径留在 `retrieval-judgment`，`retrieval-quality` 那条改为移除。

## What Changes

- **BREAKING** 检索去重两处改动：① 去重单位从**文档**（`doc_id`）改为**内容**（父块），每父块保留 1 条；② 去重位置从 rerank **之前**移到 **之后**，每父块保留**精排分最高**的一条（代表权由相关性而非融合名次决定）。无 `parent_content` 的 chunk 按自身保留。
- 候选池 `TOP_K_RETRIEVAL` 默认值改为 **30**（已先行落地：`src/config/settings.py:243`、`.env`、`README.md:283`）。**注意**：它约束的是**各支路取数**，不是精排输入的上界（后者为 `RRF_TOP_N`）—— 见 design D3 的"口径更正"。
- 补全取数观测：
  - `[retrieval] rerank done` 增 `score_max` / `score_min` / `score_p50` 与分数来源标记 `scored`（`rerank` | `fallback`；降级路径的 `1 - distance` 分数须可剔除）
  - 新增 `[retrieval] dedup done` 事件，记录 `dropped` / `kept`（**不改 `retrieval.search` 的返回契约**）
  - `[retrieval] retrieve replay` **移除** `dedup_max_per_doc` 字段（随 `RETRIEVAL_MAX_PER_DOC` 一并退出）
- 采样并产出"库内有无该内容"的**判据结论**（依据 `rerank` 分数的双形态：库内有 → 双峰断层；库内无 → 平滑无拐点）。本变更只产出结论，**不接控制流**。
- 修正 `retrieval-quality` spec 的 3 处存量 drift；supersede `retrieval-quality` 与 `retrieval-judgment` 中"按 doc_id 去重"的要求。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `retrieval-quality`: 候选池默认值与取值区间、`Cross-document aggregation` 与 `检索去重策略参数化` 两条互斥要求的消解、评测矩阵区间更新、废弃的"语义选库"要求移除。
- `retrieval-judgment`: `检索结果去重` 要求由"按 doc_id 去重、每文档保留最先出现的结果，位置在 rerank 前"改为"按内容（父块）去重，位置在 **rerank 之后**、每父块保留**精排分最高**的一条"。
- `observability-logging`: `rerank done` 增分位数字段与来源标记 `scored`、新增 `dedup done` 事件；`retrieve replay` 与「离线重放 CLI」两条要求移除 `dedup_max_per_doc`。

## Impact

**代码**

- `src/config/settings.py` — 候选池默认值（已改）；删除 `RETRIEVAL_MAX_PER_DOC`
- `src/rag/retrieval.py` — `search` 不再去重；`_dedup_by_doc_id` → `_dedup_by_parent`，位置移到精排之后（保留最高分）、截断之前；`rerank done` 增分数字段；新增 `dedup done` 事件
- `src/agents/tools/rag_tools.py` — 移除 `retrieve replay` 的 `dedup_max_per_doc` 写入
- `src/core/log_events.py` / `log_event_specs.py` — `ReplayEvent` 去掉 `dedup_max_per_doc`；`rerank done` / `dedup done` 字段登记
- `src/cli/replay_trace.py` — 去掉 `settings.RETRIEVAL_MAX_PER_DOC` 读取与 drift 的 `dedup` 轴
- `src/cli/compare_retrieval.py` — 评测网格 `[5, 10, 15]` → `[10, 30, 50]`（与 spec 对齐）
- `src/cli/eval_ragas.py` — 清理引用已作废 `compare_dedup` 的注释（悬空引用）

**测试**

- `tests/rag/test_retrieval.py` / `tests/rag/test_retrieval_dedup.py`（去重口径）、`tests/core/test_log_events.py`（日志字段）、`tests/cli/test_replay_trace.py`（replay 字段）

**文档**

- `docs/agents/glossary.md` — `dedup` / `RETRIEVAL_MAX_PER_DOC` 词条改写（单位变更）
- `docs/agents/logging-rules.md` — 新字段按开放登记制登记
- `README.md` — 配置表（已改）；`src/cli/README.md` — 评测网格描述
- `docs/agents/defensive-patterns.md` — 以 `_dedup_by_doc_id` 为例的段落随改名同步（`_dedup_by_parent`）
- `docs/adr/0001-retrieval-fetch-and-dedup-scope.md` — 本变更的决策记录；`docs/adr/0014-candidate-pool-not-rerank-input-bound.md` — 更正其两段陈述（候选池非精排输入上界；多库路径死因）

**失效资产**

- `src/cli/compare_dedup.py` — A/B 网格 `{1,2,3}` 针对文档配额，单位变更后失效（改为内容级或标记作废）

**明确不在本变更范围**

- 判据接控制流（拦截层与动作尚未决定）
- 绝对分数阈值（"不做绝对阈值"是既有决策，见 `retrieval-judgment` spec）
- 破损表格碎片（属分块问题）
