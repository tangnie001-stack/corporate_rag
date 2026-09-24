## Context

`src/rag/retrieval.py` 的取数链路**现状**是（去重在精排之前）：

```
dense(TOP_K_RETRIEVAL) + 词法(TOP_K_RETRIEVAL，PG 全文检索)
  → rrf_fusion            （retrieval.py:87-88）
  → _dedup_by_doc_id      （retrieval.py:102 hybrid / :116 非 hybrid）★ 在 rerank 之前
  → rerank_results        （retrieval.py:120-197，取前 TOP_K_RERANK）
  → rag_tools.py:177 contexts[:top_k]
```

**改后**（去重移到精排之后，按父块折叠、保留最高分 —— 见 D2）：

```
dense + 词法 → rrf_fusion
  → rerank_results        （对全部融合候选打分，不再先截断）
  → _dedup_by_parent      （按 (doc_id, 父块内容哈希) 折叠，每父块保留精排分最高的一条）
  → 截断 TOP_K_RERANK
  → rag_tools.py:177 contexts[:top_k]
```

约束与现状：

- **天花板是乘性的**：`文档数 × RETRIEVAL_MAX_PER_DOC`。实测当前 KB（2 篇文档）恒为 2；`ea84fb72`（3 篇）恒为 3。
- **去重位置在 rerank 之前**：`rerank done doc_count=<去重后条数>`（`retrieval.py:194` 记的是 rerank 的**输入**），所以精排无法在"同一文档的多个候选"之间做选择。
- **父块正文会被重复渲染**：`retrieval.py:168-172` 用 `parent_content` 覆盖 chunk 正文，`context.py:60` 渲染的正是它。实测一父块约 1998 字符、被 3.25~3.9 个 chunk 共享 —— 只要同一父块的多个 chunk 同时进入结果，就会输出逐字相同的正文。现状 `每文档 1 条` 恰好掩盖了这一点。
- **观测缺口**：`rerank done` 只记输入条数与 query 长度（`retrieval.py:191-196`）；`retrieve done` 只记最终条数（`rag_tools.py:217-223`）。**精排分数分布**与**去重丢弃量**不可见。（分路贡献 `dense_count` / `sparse_count` 已由在效 spec「稀疏支路贡献可见」要求并在 `hybrid done` 落盘，不属缺口。）
- **多库路径已死**：`retrieval.search` 的唯一生产调用方是 `rag_tools.py:133`；`kb_id` 为空时 `rag_tools.py:132` 直接不调 `search` → `search` 从不在空 `kb_id` 下被调用（`search` 自身没有 `kb_id` 空分支）。
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

### D2：去重移到 rerank 之后，每父块保留精排分最高的一条

**改后顺序**：融合 → 精排（对全部候选打分）→ 内容级去重（按 `(doc_id, 父块内容哈希)` 折叠，保留精排分最高的一条）→ 截断 `TOP_K_RERANK`。

**为什么移到精排之后**：现状去重在精排之前，等于在精排看到候选之前就替它选好了"哪条 chunk 代表这个父块"。代表权不该由 RRF 名次（融合顺序）决定，应由精排相关性决定 —— 父块正文才是最终渲染给模型的内容，代表它的应当是相关性最高的那条。这与 ADR-0001 对"配额在 rerank 之前替精排做选择"的批评是同一件事，只是对象从文档换成了父块。

**代价**：精排输入从"去重后条数"变为"全部融合候选"（实测 30~35 条）。耗时不是约束 —— ADR 实测 50 条输入 468~735 ms，`RERANK_TIMEOUT=5` 有 7 倍余量。

**残留误差（明确接受）**：精排对 `r.content`（子 chunk 正文）打分，而渲染的是 `parent_content`（父块正文），两者不完全一致。本决策只保证"同一父块只用一条、且是精排分最高的那条"，**不改变"用什么文本喂精排"这一既有行为**（ADR-0001 明确不评判 `retrieval.py:168-172` 的父块正文替换）。

**降级路径同样必须去重**：`rag_tools.py` 的 `except TimeoutError` 分支（约 `:144-176`）在精排超时时**不进入** `rerank_results`，而是用检索原始顺序手工构造 `RAGContext`。改后 `search` 不再去重 → 这条路径会把"同一父块被重复渲染"重新引入（正是 D1 要消除的现象）。因此去重 SHALL 抽成可在两条路径上复用的步骤，降级路径同样调用它并落 `dedup done`；否则本变更净引入一条新的重复渲染回归。

**代表分由调用方显式给出**：`_dedup_by_parent` 不内部自取分数，而是接受调用方给出的"代表分"——rerank 路径取 `relevance_score`（精排分最高者代表父块），精排超时降级路径取 `1 - distance`（与 `rag_tools.py` 降级分支现有的 `score=1-distance` 同源）。两条路径的分数语义不同，混用一种取值会让"保留最高分"在降级路径上退化成"保留 RRF 首条"。

### D3：`TOP_K_RETRIEVAL` 取 30

**候选**：30 / 50 / 150。

- WeKnora `DefaultRetrievalTopK = 50`；业界推荐 30~100；本仓库 `requirements_pool.md` F-05 曾提 50~150。
- 实测精排输入 50 条：耗时 468~735 ms，`RERANK_TIMEOUT=5` 有 7 倍余量 → 耗时不是约束。
- **口径更正**：`TOP_K_RETRIEVAL` 约束**各支路取数**（dense 与词法各取至多 30 条），**不是**精排输入的上界 —— hybrid 路径喂精排的是 RRF 融合结果，其上界是 `RRF_TOP_N`（当前 50；本 trace 实测融合结果 30~35 条，未构成绑定约束）。因此本变更**不主张**"精排成本降约 40%"：该说法默认了精排输入随 `TOP_K_RETRIEVAL` 缩小，而实际上 `RRF_TOP_N` 未动。若确要压缩精排成本，应下调 `RRF_TOP_N`（见 Open Questions）。**召回损失未测**。
- 已先行落地于 `src/config/settings.py:243` / `.env` / `README.md`。

**取舍已记录在 ADR-0001 的「接受的代价」与复查触发条件**（top1 落在 0.2~0.5 灰区的 query 占比升高 → 先试 50）。

### D4：观测字段的最小集

| 事件 | 新增字段 | 它能回答什么问题 |
|---|---|---|
| `rerank done` | `score_max` / `score_min` / `score_p50` + `scored`（`rerank` \| `fallback`） | 阈值能否校准、库内有无内容；降级分可剔除 |
| **`dedup done`（新事件）** | `dropped` / `kept` | 天花板是否仍在生效（精排后 − 去重后） |

**为什么去掉 `score_top1`**：`reranked` 按分数降序排列，`score_top1` 恒等于 `score_max`；同一事实记两个名字，消费方无法判断该信哪个。

**为什么加 `scored`**：`rerank failed` 降级路径用 `1 - distance` 造分（`rerank_results` 的 `except` 分支），与精排相关性分**量纲不同**。不标记来源就会把两类样本混进同一组分位数，直接污染 D5 的形态判据与任何阈值校准 —— 这是"分数可见"变成"分数误导"的典型。

`hybrid done` 的分路计数不在本表：它已由在效 spec「稀疏支路贡献可见」要求、代码已落盘（`dense_count` / `sparse_count`），本变更加它会造成同一条事实两个 owner。

**为什么去重自己落一条事件，而不是挂在 `retrieve done` 上**：D2 改后去重发生在精排之后、`rerank_results` 内部，而 `rerank_results` 只返回 `list[RAGContext]`。要把统计挂到 `retrieve done`（`rag_tools.py:217-223`），只能改返回值契约 —— 那会破坏 `tests/rag/test_retrieval.py` 的返回值断言，并把"编排关注点"塞进检索层。去重自落事件零契约影响，也天然符合 `logging-rules.md` 的开放登记制。

**不放分数全量列表**：`rerank done` 是每次 `retrieve_kb` 都落的信息级事件，全量分数会显著放大日志体积；分位数足以支撑阈值校准与双形态判别。

### D5：判据结论的产出方式（不接控制流）

用两形态假说采样验证：`score_max` 的绝对值 + 分布形状，**只取 `scored=rerank` 的样本**（降级路径的 `1 - distance` 分不参与，见 D4）。

已有初步观测（1 个 KB / 4 个 query）：

| 形态 | 触发 | `score_max` | 形状 |
|---|---|---|---|
| 双峰 | 库内**有**该内容 | 0.8337（次高 0.7245，随后骤降至 0.18） | 断层明显 |
| 平滑 | 库内**没有**该内容 | 0.1998 ~ 0.2898 | 平滑无拐点 |

样本不足以定阈值 —— 本变更只要求**采样并写出结论文档**，把判据的最终形态与是否接控制流留给后续变更。

## Risks / Trade-offs

- **[单文档占满精排窗口]** → 实测候选池按 chunk 数倾斜（`{东软:12, 腾讯:38}`，76% 来自一个文档）。缓解依赖精排：实测有效（库内有内容时相关文档以 0.8337/0.7245 排前二）。对"库内无内容"型查询，排序本就是噪声，无从缓解 —— 这属于止损信号的范畴，不是本变更能解决的。
- **[`parent_content` 缺失的 chunk 不受约束]** → 33/51 有父块。缺失时退化为按自身保留，等价于不去重；结果是这部分 chunk 仍可能重复进入结果。**接受的代价**：宁可放过，不可错杀（错杀会丢召回）。
- **[日志体积增大]** → 只加分位数，不加全量列表（D4）；新字段走 `logging-rules.md` 开放登记制。
- **[`compare_dedup.py` 已作废]** → 其网格 `{1,2,3}` 与 `RETRIEVAL_MAX_PER_DOC` 一并退出（口径改成父块级后无可调参数，无 A/B 可言）。**不可只留 TODO** —— 它评的链路已改口径，跑出来是另一件事，留着会误导后续实验。作废需在 glossary 注明。
- **[精排输入与成本]** → **确定**的放大只有一处：去重后移到精排之后，精排输入从"去重后条数"变为"全部融合候选"（上界 `RRF_TOP_N`，本 trace 实测 30~35 条）。`TOP_K_RETRIEVAL` 8→30 **不改变**该上界（融合结果被 `RRF_TOP_N` 截断），故**不主张**它带来成本变化（见 D3 的口径更正）。耗时不是约束（50 条输入 < 1s，`RERANK_TIMEOUT=5`）；**费用按输入条数计**，需在采样时记录（tasks 5.5）。
- **[`retrieval-quality` spec 的 drift 修正属于"顺手改"]** → 三处 drift（默认值 10/5、评测矩阵 5/10/15、语义选库）都不是本变更引入的，但都在同一份 spec 里且与本变更同域。**若不修，spec 会继续与代码不符**；修则扩大 diff。选择修，并在 tasks 里单列，便于 reviewer 分辨。

## Migration Plan

**与 change `prompt-layering-and-domain-binding` 的顺序**：**本变更先落地**；对端的 **P0（YAML 载体搬运，零行为差异、零文件交集）可并行开工**。对端的 P1/P2 必须排在本变更之后 —— 它要重采两次基线（prompt 快照 + RAGAS eval），而基线所测的 context 内容正是本变更在改的东西，先采会直接作废。

1. **观测先行**：先落 D4（不改行为），跑一轮真实请求确认字段可读。
2. **去重单位与位置切换**：改 `retrieval.py`（`search` 不再去重；去重移到 `rerank_results` 内、打分之后、截断之前），同步改 `tests/rag/test_retrieval.py` / `test_retrieval_dedup.py` 的去重断言（现有断言写的是 doc_id 语义与"rerank 前"，属契约变更）。
3. **文档同步**：`glossary.md` 词条、`logging-rules.md` 登记、`compare_dedup.py` 处置。
4. **采样**：用 2 个 KB（`b9e74e82` / `ea84fb72`）× 多 query 采分数分布，产出结论。
5. **spec 归档**：`retrieval-quality` / `retrieval-judgment` / `observability-logging` 的 delta 合并进在效 spec。

**回滚**：观测字段与去重单位各自独立 —— 观测字段为纯增量（回滚即删字段）；`TOP_K_RETRIEVAL` 为单配置项。**去重单位切换的回滚不再是一行**：删除 `RETRIEVAL_MAX_PER_DOC` 与 replay 的 `dedup_max_per_doc` 字段后，恢复"按 `doc_id` 去重"需同时恢复配置项、replay 写方与 `ReplayEvent`/`replay_trace.py`——回滚面 = 本变更 3.5 的清单。若需保留一行回滚能力，可选把配置项标记 `deprecated` 保留一个版本。

**关于 `RETRIEVAL_MAX_PER_DOC` 的存在性**：删除它不涉及部署风险 —— 容器与 `.env` 均未设置该键（只有 `TOP_K_RETRIEVAL` / `TOP_K_RERANK`），删后无读取方残留（清单见 tasks 3.5）。

## Open Questions

1. **判据接控制流**：拦截层（工具内算证据 / 路由层决策 / 循环外节点）与动作（告诉模型 / 路由短路 / 问用户 / 直接联网）均未定 —— ⚠ 动作**不应**放工具内：`retrieve_kb` 在 ToolNode 里并行执行，多个协程同时 ask 会撞单槽保护（`src/agents/graph/verify/ask_confirm.py:34`）。
2. **候选池 30 的召回损失**：待 D4 数据积累后复核，必要时回调 50。
3. **`ea84fb72`（121 chunk / 3 文档 / 单文档 85 chunk）未纳入先前的临时采样**：它是"单文档占满窗口"风险的最强样本，**必须**纳入正式采样（见 tasks 5.2）。
4. **`compare_dedup.py` 的去留**：改造为内容级 A/B，还是标记作废并在 glossary 注明？
5. **`RRF_TOP_N` 是否下调**：它才是精排输入的真实上界（当前 50）。若要压缩精排成本/上下文规模，应动它而非 `TOP_K_RETRIEVAL`。本变更不动，记为后续项（改动它会同时影响召回面，需单独评估）。
