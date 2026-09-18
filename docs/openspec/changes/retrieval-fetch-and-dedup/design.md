## Context

`src/rag/retrieval.py` 的取数链路当前是：

```
dense(TOP_K_RETRIEVAL) + bm25(TOP_K_RETRIEVAL)
  → rrf_fusion            （retrieval.py:87-88）
  → _dedup_by_doc_id      （retrieval.py:95 hybrid / :116 非 hybrid）★ 在 rerank 之前
  → rerank_results        （retrieval.py:120-197，取前 TOP_K_RERANK）
  → rag_tools.py:180 contexts[:top_k]
```

约束与现状：

- **天花板是乘性的**：`文档数 × RETRIEVAL_MAX_PER_DOC`。实测当前 KB（2 篇文档）恒为 2；`ea84fb72`（3 篇）恒为 3。
- **去重位置在 rerank 之前**：`rerank done doc_count=<去重后条数>`（`retrieval.py:194` 记的是 rerank 的**输入**），所以精排无法在"同一文档的多个候选"之间做选择。
- **父块正文会被重复渲染**：`retrieval.py:168-172` 用 `parent_content` 覆盖 chunk 正文，`context.py:60` 渲染的正是它。实测一父块约 1998 字符、被 3.25~3.9 个 chunk 共享 —— 只要同一父块的多个 chunk 同时进入结果，就会输出逐字相同的正文。现状 `每文档 1 条` 恰好掩盖了这一点。
- **观测缺口**：`hybrid done` 只记融合后条数（`retrieval.py:89-94`）；`rerank done` 只记输入条数与 query 长度（`:191-196`）；`retrieve done` 只记最终条数（`rag_tools.py:217-223`）。分数、分路贡献、去重丢弃量全部不可见。
- **多库路径已死**：`retrieval.search` 的唯一生产调用方是 `rag_tools.py:136` 且 `kb_id` 恒非空（`:135` 守卫）→ `retrieval.py:98-105` 的 `not kb_id` 分支生产不可达。
- **规模前提**（2026-09-18 确认）：正式测试几十文档 / 生产几百 / 长期千；实测每文档 25~40 chunk（单个年报可达 85 chunk）。三种规模下 `文档数 × N` 均非绑定约束，`TOP_K_RETRIEVAL` 才是。

决策记录见 `docs/adr/0001-retrieval-fetch-and-dedup-scope.md`。

## Goals / Non-Goals

**Goals:**

- 消除"文档数 × 每文档条数"这一取数天花板，让精排窗口（5）能被填满。
- 让精排重新获得"在文档内部精选"的能力（单事实/分析类查询可拿到目标文档的多个不同章节）。
- 让取数过程可观测：分路贡献、分数分布、去重丢弃量。
- 产出一份可复核的"库内有无该内容"判据结论，供后续止损信号使用。
- 修正 `retrieval-quality` spec 的存量 drift。

**Non-Goals:**

- 不实现止损判据接控制流（拦截层与动作尚未决定）。
- 不引入绝对分数阈值（既有决策，见 `retrieval-judgment` spec）。
- 不处理 BM25 静默失效与索引持久化（第 3 层，独立缺陷）。
- 不引入 MMR（排序见 ADR-0001 复查触发条件）。
- 不改 `TOP_K_RERANK`（保持 5）。
- 不改 `parent_content` 覆盖 chunk 正文这一行为本身。

## Decisions

### D1：去重单位从 `doc_id` 改为内容（父块）

**做法**：`_dedup_by_doc_id` 的去重键由 `metadata["doc_id"]` 改为父块标识；每父块保留 1 条。

**候选**：

| 方案 | 代价 | 收益 |
|---|---|---|
| A 保留文档配额，调大 N | 保留"配额在 rerank 前替精排做选择" | 改动最小 |
| B 内容级去重 | 单文档可占满窗口，缓解依赖 rerank | 消除天花板；构造性排除重复渲染 |
| C MMR（λ=0.7） | 需相似度计算 + 改三段 + 重采基线 | 多样性按查询自适应 |

**选 B。** 核心理由：**重复的单位是内容，不是文档**。按文档配额没有消除重复，只是限制了同一文档能贡献几条 —— 而同一文档贡献的多个 chunk 恰恰可能是**不同章节、互不重复**的内容。

**父块标识的取法**：键为 `(doc_id, hash(parent_content))`；`parent_content` 缺失（实测 33/51 有）时退化为按该 chunk 自身保留（等价于不去重）。

- **为什么把 `doc_id` 纳入键**：`parent_content` 是父块的**文本**，不是父块的**身份**。跨文档的样板文本（年报的"重要提示""免责声明"）若逐字相同，只按内容哈希会把它判成同一个父块 → **静默丢掉一个真实候选**。实测当前两个 KB 的跨文档重复为 0（85 个有父块的 chunk / 42 个不同父块），但那是数据巧合，不是机制保证。
- **为什么不用 `heading_path` 当键**：实测 `(doc_id, heading_path)` 在 **11/51 组**里对应多个不同的父块（21.6%）。`heading_path` 是章节路径，不是父块标识。
- **为什么不做全文 key**：`parent_content` 约 2000 字符，直接做 dict key 不经济；哈希后即可。

**N 不引入**：任何固定 N 都是同一错误的缩放 —— N ≤ 2 仍构成天花板；N ≥ 5 时配额失效（`文档数 × 5 ≥ TOP_K_RERANK` 恒成立）。引入一个永不生效的旋钮不如删掉。

### D2：去重仍在 rerank 之前，但不再按文档切

保留"去重后精排"的顺序（与既有 spec 一致，且能减少精排输入量）。区别在于：内容级去重只折叠**重复内容**，不折叠"同一文档的不同内容"—— 因此精排仍能看到目标文档的多个不同章节。真正的"谁最相关"由精排决定。

### D3：`TOP_K_RETRIEVAL` 取 30

**候选**：30 / 50 / 150。

- WeKnora `DefaultRetrievalTopK = 50`；业界推荐 30~100；本仓库 `requirements_pool.md` F-05 曾提 50~150。
- 实测候选池 50：精排耗时 468~735 ms，`RERANK_TIMEOUT=5` 有 7 倍余量 → 耗时不是约束。
- 取 30 的依据是**精排成本**（按输入文档数计费，降约 40%）；**召回损失未测**。
- 已先行落地于 `src/config/settings.py:179` / `.env` / `README.md`。

**取舍已记录在 ADR-0001 的「接受的代价」与复查触发条件**（top1 落在 0.2~0.5 灰区的 query 占比升高 → 先试 50）。

### D4：观测字段的最小集

| 事件 | 新增字段 | 它能回答什么问题 |
|---|---|---|
| `hybrid done` | `dense_count` / `bm25_count` | BM25 支路是否还活着、贡献多少（第 3 层的前置观测） |
| `rerank done` | `score_top1` / `score_max` / `score_min` / `score_p50` | 阈值能否校准、库内有无内容 |
| **`dedup done`（新事件）** | `dropped` / `kept` | 天花板是否仍在生效（融合后 − 去重后） |

**为什么去重自己落一条事件，而不是挂在 `retrieve done` 上**：去重发生在 `retrieval.search` 内部，而 `search` 只返回 `list[ChunkResult]`。要把统计挂到 `retrieve done`（`rag_tools.py:217-223`），只能改 `search` 的返回契约 —— 那会破坏 `tests/rag/test_retrieval.py` 的返回值断言，并把"编排关注点"塞进检索层。去重自落事件零契约影响，也天然符合 `logging-rules.md` 的开放登记制。

**不放分数全量列表**：`rerank done` 是每次 `retrieve_kb` 都落的信息级事件，全量分数会显著放大日志体积；分位数足以支撑阈值校准与双形态判别。

### D5：判据结论的产出方式（不接控制流）

用两形态假说采样验证：`rerank top1` 的绝对值 + 分布形状。

已有初步观测（1 个 KB / 4 个 query）：

| 形态 | 触发 | top1 | 形状 |
|---|---|---|---|
| 双峰 | 库内**有**该内容 | 0.8337（次高 0.7245，随后骤降至 0.18） | 断层明显 |
| 平滑 | 库内**没有**该内容 | 0.1998 ~ 0.2898 | 平滑无拐点 |

样本不足以定阈值 —— 本变更只要求**采样并写出结论文档**，把判据的最终形态与是否接控制流留给后续变更。

## Risks / Trade-offs

- **[单文档占满精排窗口]** → 实测候选池按 chunk 数倾斜（`{东软:12, 腾讯:38}`，76% 来自一个文档）。缓解依赖精排：实测有效（库内有内容时相关文档以 0.8337/0.7245 排前二）。对"库内无内容"型查询，排序本就是噪声，无从缓解 —— 这属于止损信号的范畴，不是本变更能解决的。
- **[`parent_content` 缺失的 chunk 不受约束]** → 33/51 有父块。缺失时退化为按自身保留，等价于不去重；结果是这部分 chunk 仍可能重复进入结果。**接受的代价**：宁可放过，不可错杀（错杀会丢召回）。
- **[日志体积增大]** → 只加分位数，不加全量列表（D4）；新字段走 `logging-rules.md` 开放登记制。
- **[`compare_dedup.py` 已作废]** → 其网格 `{1,2,3}` 与 `RETRIEVAL_MAX_PER_DOC` 一并退出（口径改成父块级后无可调参数，无 A/B 可言）。**不可只留 TODO** —— 它评的链路已改口径，跑出来是另一件事，留着会误导后续实验。作废需在 glossary 注明。
- **[精排成本上升]** → 候选池 8→30 使精排输入增大约 3.75 倍（按输入文档数计费）。30 是为成本取的折中；若召回受损需回调（见 D3）。
- **[`retrieval-quality` spec 的 drift 修正属于"顺手改"]** → 三处 drift（默认值 10/5、评测矩阵 5/10/15、语义选库）都不是本变更引入的，但都在同一份 spec 里且与本变更同域。**若不修，spec 会继续与代码不符**；修则扩大 diff。选择修，并在 tasks 里单列，便于 reviewer 分辨。

## Migration Plan

**与 change `prompt-layering-and-domain-binding` 的顺序**：**本变更先落地**；对端的 **P0（YAML 载体搬运，零行为差异、零文件交集）可并行开工**。对端的 P1/P2 必须排在本变更之后 —— 它要重采两次基线（prompt 快照 + RAGAS eval），而基线所测的 context 内容正是本变更在改的东西，先采会直接作废。

1. **观测先行**：先落 D4（不改行为），跑一轮真实请求确认字段可读。
2. **去重单位切换**：改 `retrieval.py`，同步改 `tests/rag/test_retrieval.py` 的去重断言（现有断言写的是 doc_id 语义，属契约变更）。
3. **文档同步**：`glossary.md` 词条、`logging-rules.md` 登记、`compare_dedup.py` 处置。
4. **采样**：用 2 个 KB（`b9e74e82` / `ea84fb72`）× 多 query 采分数分布，产出结论。
5. **spec 归档**：`retrieval-quality` / `retrieval-judgment` / `observability-logging` 的 delta 合并进在效 spec。

**回滚**：观测字段与去重单位各自独立 —— 观测字段为纯增量（回滚即删字段）；去重单位切换为单函数改动（回滚即恢复按 `doc_id` 去重）。`TOP_K_RETRIEVAL` 为单配置项。三者无耦合，可分别回滚。

## Open Questions

1. **判据接控制流**：拦截层（工具内算证据 / 路由层决策 / 循环外节点）与动作（告诉模型 / 路由短路 / 问用户 / 直接联网）均未定 —— ⚠ 动作**不应**放工具内：`retrieve_kb` 在 ToolNode 里并行执行，多个协程同时 ask 会撞单槽保护（`src/agents/graph/verify/ask_confirm.py:34`）。
2. **候选池 30 的召回损失**：待 D4 数据积累后复核，必要时回调 50。
3. **`ea84fb72`（121 chunk / 3 文档 / 单文档 85 chunk）未纳入先前的临时采样**：它是"单文档占满窗口"风险的最强样本，**必须**纳入正式采样（见 tasks 5.2）。
4. **`compare_dedup.py` 的去留**：改造为内容级 A/B，还是标记作废并在 glossary 注明？
