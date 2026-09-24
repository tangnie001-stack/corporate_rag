# ADR-0001：检索取数口径 —— 取消每文档配额，改用大候选池 + 内容级去重

- **Status**：Accepted（**「候选池 30 的成本收益」与「多库路径的 `not kb_id` 分支」两段陈述已被 ADR-0014 更正**，其余决策与陈述仍有效）
- **Date**：2026-09-18
- **Deciders**：用户（决策）；Claude（调研与实测）

## 背景与问题

### 触发：在效 spec 内部冲突

同一份在效 spec 里有两条互斥的要求，当「文档数 < TOP_K_RERANK」时不可同时满足：

| 位置 | 要求 |
|---|---|
| `docs/openspec/specs/retrieval-quality/spec.md:24-25`（Cross-document aggregation） | RAG chain SHALL aggregate context from up to `TOP_K_RERANK` chunks **regardless of which document they originate from** |
| 同文件 `:121-123`（检索去重策略参数化） | 按 `doc_id` 去重，每文档最多 `RETRIEVAL_MAX_PER_DOC` 条（默认 1） |

### 现状机制

- `src/rag/retrieval.py:95`（hybrid 分支）与 `:116`（非 hybrid）调用 `_dedup_by_doc_id`，**位置在 `rerank_results` 之前** —— 精排看不到被丢弃的候选。
- 天花板公式：`文档数 × RETRIEVAL_MAX_PER_DOC`，与 query 无关。

### 证据

**trace `c54ce259`（2026-09-16，`app_2026-09-16.log`）**：4 轮 query 各不相同，结果恒为 2。

```
[retrieval] hybrid done result_count=8
[retrieval] rerank done doc_count=2        ← rerank 的【输入】（retrieval.py:194）
[retrieval] retrieve done iteration=1..4 result_count=2
```

**实测（2026-09-18，`kb_b9e74e82…`，`TOP_K_RETRIEVAL=50`）**：

| query | 候选池 | 文档分布 | rerank top1 | dedup@1 结果 |
|---|---|---|---|---|
| 查看一下东软这几年的年报，分析一下结果 | 50 | `{东软:12, 腾讯:38}` | 0.2525（平滑无拐点） | 2 |
| 东软集团 2024年年度报告 营业收入… | 50 | `{东软:12, 腾讯:38}` | 0.2898（平滑无拐点） | 2 |
| 东软集团 2023年年度报告 主要会计数据… | 50 | `{东软:13, 腾讯:37}` | 0.1998（平滑无拐点） | 2 |
| 东软集团 2025年第一季度报告 营业收入… | 50 | `{东软:13, 腾讯:37}` | **0.8337**（0.7245 次高，随后骤降至 0.18） | 2 |

**原始动机已消失**：

- `docs/openspec/changes/archive/2026-09-02-web-search-fallback/design.md:75` 记录去重的动机是"KB 存在重复文档（neusoft_2025_q1.pdf×4、tencent_2024_annual.pdf×2），不去重会占满 top-8、漏掉其他文档内容"。实测当前 KB **无重复文档**：`b9e74e82` = 2 文档 / 51 chunk；`ea84fb72` = 3 文档 / 121 chunk。
- 跨库检索已废弃：`retrieval.search` 的唯一生产调用方是 `src/agents/tools/rag_tools.py:136`，`kb_id` 恒非空（`:135` 守卫）→ `retrieval.py:98-105` 的 `not kb_id` 分支生产不可达。
- `RETRIEVAL_MAX_PER_DOC` 从未产出实测结论：`docs/openspec/changes/archive/2026-09-12-retrieval-quality-signals/tasks.md` 的 2.4（A/B 跑 RAGAS）与 2.5（据结论定 N）**未勾选即归档**。

**规模前提（2026-09-18 确认）**：正式测试几十文档 / 生产几百 / 长期千；实测每文档 25~40 chunk（单个年报可达 85 chunk）。三种规模下 `文档数 × N` 都不是绑定约束，`TOP_K_RETRIEVAL` 才是。

**业界与同领域参照**：候选池 30~100、精排后 5~10（《10 万文档 RAG 落地实战》）；WeKnora `DefaultRetrievalTopK = 50`（`internal/types/retrieval_config.go:40-44`），`RerankTopK` 租户 10 / agent 5。**两家均无"每文档保留 N 条"这一类约束**；WeKnora 的有效去重是"每父块 1 条"（`internal/agent/tools/knowledge_search.go:770-776` 的 `parent:` key）。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A | 保留每文档配额，调大 N（2 / 3 / 5） | 保留"配额在 rerank 前替精排做选择"这一机制 | 改动最小 |
| B | 取消每文档配额；去重单位改为**内容（父块）**；候选池 30 | 单文档可占满窗口，缓解依赖 rerank | 消除天花板；与 spec:25 一致；允许文档内深度 |
| C | 取消配额，改用 MMR（λ=0.7，Jaccard 冗余惩罚，WeKnora `applyMMR` 路线） | 需候选相似度计算、改检索三段、重采 eval 基线 | 多样性按查询自适应 |

## 决策

**选 B。** 具体为：

1. `RETRIEVAL_MAX_PER_DOC` 的**单位从文档（`doc_id`）改为内容（父块）**，每父块保留 1 条；无 `parent_content` 的 chunk 退化为按自身保留。
2. 候选池 `TOP_K_RETRIEVAL` 定为 **30**（`src/config/settings.py` 默认值与 `.env` 同步）。
3. 精排后条数 `TOP_K_RERANK` 保持 **5** 不变。

## 理由

**最关键的一行：去重的单位选错了 —— 重复的单位是内容，不是文档。**

实测父块正文约 1998 字符，且被 3.25~3.9 个 chunk 共享（`b9e74e82`：38 chunk ÷ 10 父块、13 ÷ 4；`ea84fb72`：85 ÷ 22、23 ÷ 7、13 ÷ 4）。而 `retrieval.py:168-172` 在精排时把 chunk 正文替换为父块正文，`context.py:60` 渲染的正是它 —— 于是**同一父块的多个 chunk 会渲染出逐字相同的正文**。按文档配额是在错误的粒度上切：它没有消除重复，只是限制了同一文档能贡献几条。

**配额在 rerank 之前生效，等于替精排做了选择，而且两个方向都打反。**

- 单事实 / 分析类查询的候选**天然集中**在目标文档（一份年报 85 chunk）→ 配额砍得最狠，而这恰是最需要文档内深度的时候。
- 对比类查询的候选**天然分散**在多家公司 → 配额几乎不作用，而这恰是最需要跨文档广度的时候。

**数字依据**：取消文档配额后，单文档可达数十条候选；父块级去重把它折成互不重复的 10~22 段正文（按实测父块数）。这是"文档内深度"的真实可达上限，而现状是 **1 条**。

**为什么不选 A**：任何固定 N 都是同一个错误的缩放。N ≤ 2 时仍构成天花板；N ≥ 5 时配额失效（`文档数 ≥ 1` 使 `文档数 × 5 ≥ TOP_K_RERANK` 恒成立）—— 引入一个永不生效的旋钮不如删掉。

**为什么不选 C（本轮）**：MMR 的自适应多样性在"对比类查询常态化"的规模下确有价值，但它是在取消配额**之后**才需要评估的增量，且需要候选相似度计算与 eval 基线重采。先去掉错的，再评估加对的。

## 后果

**正面**：

- 精排窗口（5）能够填满 —— 现状最多用掉 2~3。
- 单事实 / 分析类查询可拿到目标文档的多个不同章节 —— 现状恒为 1 条。
- 消除 `doc_id` 配额对 `retrieval-quality` spec:25 的违反。
- 候选池 30 落在业界区间 30~100 内。

**负面 / 接受的代价**：

- **单文档可占满窗口**：实测候选池按 chunk 数倾斜（`{东软:12, 腾讯:38}`，76% 来自一个文档）。缓解依赖 rerank —— 实测有效（库内有内容的那条 query，相关文档以 0.8337 / 0.7245 排在前二），但"库内无内容"型查询的排序本就是噪声，无从缓解。
- 精排输入增大，成本上升（按输入文档数计费）。耗时不是问题：实测 50 条输入 468~735 ms，`RERANK_TIMEOUT=5` 有 7 倍余量。
- `RETRIEVAL_MAX_PER_DOC` 被**删除**（不留失效旋钮 —— 任何固定 N 都是同一错误的缩放）；`[retrieval] retrieve replay` 事件的 `dedup_max_per_doc` 字段（`src/rag/retrieval.py:197`）一并移除，避免输出已无意义的值污染重放对照。
- 术语层面：删掉 `docs/agents/glossary.md` 的 `dedup` 与 `RETRIEVAL_MAX_PER_DOC` 两个词条，新增「**父块级去重**」（函数同时改名为 `_dedup_by_parent`）。理由：`dedup` 一名会同时指向前后两种口径，读者必须靠历史说明才能读懂，违反 glossary"只描述现状"的规约；且"去重"在本项目已有三个层次（引用展示去重 / RRF 候选去重 / 父块级去重），各需精确词条。
- `src/cli/compare_dedup.py` **作废**（口径改成父块级后无可调参数，无 A/B 可言），并在 glossary 注明；不可只留 TODO（它评的链路已改口径，跑出来是另一件事）。
- 需 supersede `docs/openspec/specs/retrieval-quality/spec.md:121-133`（检索去重策略参数化）。
- **候选池 30 目前无实测依据**：实测只覆盖 50。取 30 的收益是精排成本降约 40%，而召回损失未测 —— 需在补全分数观测后复核。

**不解决的问题**（避免后人误以为本 ADR 管了它）：

- 不解决"库里到底有没有"的判据 —— 属止损信号，另项。
- 不解决破损表格碎片 —— 属分块问题。
- 不改变"不做绝对分数阈值"这条既有决策（`retrieval-quality` spec:23）。
- 不评判 `retrieval.py:168-172` 用父块正文替换 chunk 正文本身是否合理；本决策只保证同一父块不被渲染两次。

## 复查触发条件

- 生产语料（几百 ~ 千文档）上线后，若出现"单文档占满精排窗口"的可观测现象 → 评估 MMR 或引入软性多样性控制。
- 若 top1 分数落在 0.2~0.5 灰区的 query 占比升高 → 上调候选池（先试 50）。
- 若 KB 重新出现重复上传（同一文件多个 `doc_id`）→ 需要内容级近重复检测（WeKnora 的 content signature 路线），本 ADR 的去重不覆盖该场景。
- 若 `TOP_K_RERANK` 或上下文预算调整，需复核"父块级去重 1 条"是否仍合适。
