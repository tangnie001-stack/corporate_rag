## Why

B1（跨轮历史摘要，归档于 `docs/openspec/changes/archive/2026-10-08-cross-turn-history-summary/`）已合并并**完成活体冒烟**，线上第一次有了真实的 token 数据（冒烟实测：注入历史 `usage_in=18336`、摘要产出 `covered=38 tokens=361`）。

拿这批实测数据回量时，发现 B1 与 Phase A 的取值/约束**有 5 处偏离本域选型基线**（WeKnora / LangBot / deepseek-harness / CowAgent，见 `docs/agents/selection-baseline.md` 的对齐状态表）。其中**三处会实际损害效果**、一处是结构偏薄、一处源于"时代变了"：

1. **「严格不增长」比基线过严**（`specs/history-summarization/spec.md:124` 现为无例外的硬约束）。基线 WeKnora 的原文是「must not be longer than the previous summary **unless genuinely new facts require it**」。**一条只减不增的摘要在长会话里会持续折叠仍有用的旧信息**，最终密度饱和、旧信息被永久压掉（原文虽仍在 Redis，但模型看不到）。
2. **预算与摘要上限的量级比基线小一个数量级**：我们 `HISTORY_TOKEN_BUDGET=16384` / `SUMMARY_MAX_TOKENS=2048`；基线为「窗口 − 16384」（约 11 万，WeKnora）/ 摘要上限 8192~13107。**根因不是算错，而是前提变了**——保守值定于 Phase A「超出只能丢弃」的时代；B1 有了摘要层后，更旧的部分能压成摘要**托住**，放大预算的风险结构已经不同。
3. **失败不重试**（`design.md:65` 现为"不重试，靠下一轮触阈自然重试"）。基线 WeKnora 重试 2 次（每次 60s）、CowAgent 3 次。**我们的漏洞**：用户问完就走（很常见）时，摘要**永久缺失**，那句"下一轮自然重试"永远等不到。
4. **摘要边界表示偏薄**：`covered` 只有「消息条数」，基线 deepseek-harness 记 `shadowedSeqs` + `shadowedRange` + `sourceEventSeqs`。**风险**：一旦将来做历史清理（头部被删），`covered` 会**错位**——而它正是需求池 F-39 指定的 Phase D「按需回读原文」的切点锚点。
5. **摘要额度与保留尾部的分配规则未显式**：当前 `SUMMARY_MAX_TOKENS=2048` 偏小的直接原因是「摘要段 + 尾部 ≤ `HISTORY_TOKEN_BUDGET`」的聚合约束下尾部占了绝大部分。若按基线把上限提高，**必须同时定义预算内的分配规则**，否则摘要变大就会挤掉尾部。

## What Changes

### 一、标定（数据驱动，本次的入口）

- 用 **Langfuse 的线上 token 分布**（B1 上线后已有真实数据）重估三个常量并**对齐基线量级**：`HISTORY_TOKEN_BUDGET`、`HISTORY_SUMMARY_TRIGGER_TOKENS`、`SUMMARY_MAX_TOKENS`。
- 标定**须留下方法与数据**（取哪些会话/区间、分位数怎么算、为什么取该值），使后人可复算——不接受"凭感觉调大"。

### 二、修复 4 项

1. **放宽「不增长」**：由"一律不得比上一版更长"改为「**除非确有新事实，否则不得比上一版更长**」（对齐基线措辞）。**硬兜底保留**：不变量①「必须小于被丢弃段」不变，它卡住上限。
2. **失败同轮重试**：摘要生成失败时**至少重试一次**（对齐 WeKnora/CowAgent），并保留 best-effort 与降级语义（重试仍失败 → 记降级 + 回退纯裁剪，不阻塞用户）。
3. **边界可对齐**：摘要持久化载荷在 `covered`（条数）之外**增补来源指纹**（如被覆盖段首条消息的稳定标识/hash），使「摘要 ↔ 它所覆盖的历史段」**可校验**；为 Phase D 的回读锚点提供正确性前提。
4. **摘要与尾部的额度分配**：显式定义 `HISTORY_TOKEN_BUDGET` 在「摘要段」与「保留尾部」之间的分配规则（含摘要上限与尾部下限的关系），使标定后的取值**自洽**；聚合约束「摘要段 + 尾部 + 固定开销 ≤ 预算」保持不变。

### 三、同步与登记

- `docs/agents/selection-baseline.md` 的**对齐状态表**同步本次状态变化（3 行 ⚠️ → ✅）。
- 若标定结果改变了既有 spec 的数值承诺，同步 `docs/openspec/specs/context-budget/spec.md` 与 `history-summarization` 主规格。

## Capabilities

### New Capabilities

（无——本次是对既有能力的取值标定与约束放宽/加固。）

### Modified Capabilities

- `context-budget`: 预算取值由"保守初值"改为**按线上实测标定**，并显式定义**预算在摘要段与保留尾部之间的分配规则**。
- `history-summarization`: ①「不增长」由无例外改为**"确有新事实"例外**；② 生成失败**至少重试一次**；③ 持久化载荷在覆盖条数之外**增补来源指纹**，使覆盖边界**可校验**。

## Impact

- **代码**：`src/config/const.py`（三常量取值 + 分配规则常量）、`src/chat/history_summary.py`（不增长校验放宽 + 指纹计算）、`src/chat/summary_store.py`（载荷结构加指纹）、`src/services/summary_scheduler.py`（重试）、`src/agents/graph/agent_node.py`（额度分配）。
- **依赖**：无新增。
- **行为**：① 更旧历史在长会话中**不再被无谓折叠**；② 摘要在用户只问一轮时**也能生成**；③ 注入上下文的历史量**可能显著变大**（预算提高后保留更多原文）——这是本次**主要预期变化**，须在验收时用 Langfuse 复核成本（token 消耗与延迟）。
- **数据**：Redis `chat_summary:{session_id}` 载荷新增指纹字段 ⇒ **需兼容旧载荷**（缺指纹时按"无指纹"处理，不得因此丢弃既有摘要）。
- **测试**：不增长例外的正/负用例、重试路径（含重试后仍失败）、指纹写入与校验、旧载荷兼容、分配规则边界（摘要与尾部互相挤压）。
- **文档**：`selection-baseline.md` 状态表；若数值承诺变了则同步主规格。

## 明确不做（本变更范围外）

- **历史存储清理/裁剪**（`chat_history:{session_id}` 目前仅受 TTL 约束）：ADR-0018「不解决的问题」已点名，**另开小变更**处理。本次只做"边界指纹"为其铺路。
- **L1 轮内消息级瘦身**：B1 已判定作用面几乎为零；若将来采用基线做法，须先推翻 Phase A 的「当前轮检索证据不被裁」不变量。
- **长期记忆（Phase C）**：底座形态归 ADR-0018，另行立项。
- **per-model 窗口注册表与离线探针**：B1 的阈值是绝对量、未依赖窗口数值；本次标定基于**线上实测 token 分布**而非窗口推导。
- **改写已归档的 Phase A / B1 变更**：归档不可回改；本次以新变更承接。
