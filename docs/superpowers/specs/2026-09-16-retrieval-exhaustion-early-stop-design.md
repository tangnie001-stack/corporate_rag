# 检索耗尽判定与提前止损 — 设计提案

生成日期：2026-09-16
证据来源：trace `c54ce259-7100-4eb9-bfa7-bd0ecef15f69` + KB `b9e74e820e0a4bad8472304446e54f5c` 实测 + 本仓库代码
配套调研：`docs/tmp/deep-research-trace-c54ce259.md`、`docs/tmp/deep-research-tavily-timing.md`

## 1. 问题陈述

### 1.1 现象（有 trace 证据）

用户问「查看一下东软这几年的年报，分析一下结果」，系统跑了 **5 轮 KB 检索 + 5 次 LLM 调用**，每轮只拿到 2 条，全部无法回答，直到 `MAX_AGENT_ITERATIONS=5` 触顶才停下来改走联网。

| 轮 | model_turn | 检索 | 结果数 | usage_in | usage_out | query |
|---|---|---|---|---|---|---|
| 1 | 2565ms | 2137ms | 2 | 4295 | 66 | 东软集团 年报 营业收入 净利润 2021 2022 2023 2024 |
| 2 | 1944ms | 448ms | 2 | 5851 | 56 | 东软集团 2024年年度报告 营业收入 净利润 经营现金流 总资产 |
| 3 | 3316ms | 416ms | 2 | 7397 | 56 | 东软集团 2023年年度报告 主要会计数据 营业收入 归母净利润 |
| 4 | 2301ms | 398ms | 2 | 8938 | 57 | 东软集团 2025年第一季度报告 营业收入 净利润 同比 主要会计数据 |
| 5 | 3795ms | — | — | 10679 | 62 | （触顶，无工具调用） |

**浪费量化**：3 轮 LLM 调用（约 9.4s）+ 2 次检索（约 0.8s）+ 3 轮上下文膨胀（`usage_in` 5851 → 10679）。

### 1.2 根因（已验证，非推断）

**KB 实际内容**（ChromaDB 直读，51 chunk）：

| 公司 | year | report_period | chunks |
|---|---|---|---|
| 腾讯控股有限公司 | 2024 | 二零二四年年度 | 38 |
| 东软集团股份有限公司 | 2025 | 2025年第一季度 | 13 |

用户要的是**东软的年报、多年份**；库里是**东软 2025 一季报**（季报非年报，仅 1 期）+ **腾讯 2024 年报**（无关公司）。**东软年报 0/3 覆盖。**

**`result_count=2` 恒定的机制**：KB 只有 2 份文档，`_dedup_by_doc_id` 的 `RETRIEVAL_MAX_PER_DOC=1`（每文档至多 1 条）使上限恒为 2，与 query 无关。因此该 2 条必然是「1 条东软 + 1 条腾讯」——**每轮上下文有一半来自无关公司**。

**结论：A 类（库里没有），且是极端版。5 轮检索在数学上不可能成功。**

### 1.3 为什么没能在第 1 轮就止损

模型**无法区分**两种"结果不够"：

| 状态 | 特征 | 正确动作 |
|---|---|---|
| A 库里没有 | 换 query 后结果集不变 | 停止检索，转门 2 |
| B 问法不对 | 换 query 后结果集变化 | 允许再试 |

它没有"库里有什么"的信息（不知道 KB 只有 2 份文档、不知道 `RETRIEVAL_MAX_PER_DOC=1` 造成的上限）。**它的重试是理性但徒劳的。**

**而系统里本可以有的那个信号没生效**，原因见 §1.4。

### 1.4 两个互相掩盖的代码缺陷

**缺陷 1 — `compute_missing` 参照集传错，生产环境恒空**

`src/agents/tools/rag_tools.py:129`：

```python
candidates = await derive_candidate_years([kb_id])      # = KB 覆盖年份 ∪ 最近 3 年
parsed = await parse_temporal(query, candidates, ...)   # 契约：parsed["years"] ⊆ candidates
ctx.missing_years = compute_missing(parsed["years"], candidates)
#                                    ↑ temporal.py:30 文档："covered: 知识库实际覆盖年份列表"
```

`parsed["years"]` 取自 `candidates`，再与 `candidates` 求差 → **恒为 `[]`**。

`derive_candidate_years` 的注释「KB 只覆盖 2024 时"这几年"也能解析出 [2023, 2025] 等缺失年份触发联网询问，**否则核心 bug 修不掉**」表明作者本意是让它报出缺失，但 `candidates` 被同时用作两个角色：
- LLM 的**候选选择集**（需含 recent-N，否则"这几年"解析不出年份）
- 缺失判定的**覆盖参照集**（需**只含 KB 实际覆盖**）

**缺陷 2 — `ctx.missing_years` 写而不读**

全 `src/` 仅 `rag_tools.py:129` 写入、`request_context.py:50` 定义，**无读取方**。`_ask_web_confirm` 收到的 `missing` 来自 `regen_decision.py:72`，是**基于答案文本**的 `completeness_check(required, answer)` 结果。

**缺陷 1 被缺陷 2 掩盖**：没人读 → 没人发现它恒空。测试亦未覆盖——`test_rag_tools.py:245` 断言 `ctx.missing_years == [2023]`，但 mock 了 `parse_temporal` 返回候选集外年份，属**生产不可达状态**。

### 1.5 为什么止损只能发生在循环末尾

```
ctx.temporal_years  →  required              （检索阶段已有 ✅）
answer 文本          →  extract_years()       （循环结束后才有 ❌）
missing = required - covered                 （依赖答案 → 位置被锁在末尾）
```

**这就是"必须在第 5 轮触顶后才止损"的结构性原因**：判据依赖答案，答案在循环末尾。而那个本可脱离答案、在检索阶段判定的字段既算错又无人消费。

## 2. 目标与非目标

### 目标

1. **在检索阶段**就能判定「库里没有」，不必等答案、不必等触顶
2. 判定后**停止无效重试**，进入"去哪"的决策
3. 把"库里有没有"（**代码判**）与"要不要库外信息"（**模型判**）的职责分开
4. 修复 §1.4 的两个缺陷

### 非目标（YAGNI 明确排除）

- ❌ 不改检索算法（rerank 模型、RRF 权重、分块策略）——那是 B 类问题的修法
- ❌ 不改图结构（不加节点/条件边）——`agent-loop-hardening` 已确立"加校验不动画图"
- ❌ 不新增 LLM 调用（判定必须是纯代码）
- ❌ 不改 `RETRIEVAL_MAX_PER_DOC` 的默认值（去重语义本身是对的，问题不在它）
- ❌ 不做"检索前完全跳过检索"（仍应检索一轮，取回库里已有的部分数据）

## 3. 设计

### 3.1 判据：两个层级，任一命中即"耗尽"

| 判据 | 含义 | 计算方式 | 成本 |
|---|---|---|---|
| **① KB 覆盖缺失** | 要求的年份，库里根本没有 | `required_years − kb_covered_years` 非空 | 纯代码（读已缓存元数据） |
| **② 结果集无新增** | 换了 query 拿到同样的内容 | 本轮 chunk_id 集合与上一轮**完全重合** | 纯代码（集合比较） |

**①** 回答"库里有没有"；**②** 回答"再搜有没有新东西"。任一成立 ⇒ 重试无意义。

**为什么两个都要**：
- 只有 ① 会漏判"库里年份覆盖全、但目标内容不在"的情况
- 只有 ② 需要多花一轮检索才能判定（① 在第 1 轮前就能判）

### 3.2 修缺陷：`missing_years` 的参照集必须是 KB 实际覆盖

`derive_candidate_years` 拆成两个函数（职责分离）：

```python
async def derive_kb_covered_years(kb_ids: list[str]) -> list[int]:
    """从 KB 文档元数据聚合实际覆盖年份（不含 recent-N 并入）。"""
    # 现有 derive_candidate_years 的前半段，去掉 recent-N 循环

async def derive_candidate_years(kb_ids: list[str]) -> list[int]:
    """LLM 候选选择集 = KB 覆盖 ∪ 最近 N 年（现有语义不变）。"""
    return sorted(set(await derive_kb_covered_years(kb_ids)) | {recent_n...})
```

`rag_tools.py` 改为：

```python
kb_covered = await derive_kb_covered_years([kb_id])
candidates = _with_recent_years(kb_covered)          # 不再重复查 DB
parsed = await parse_temporal(query, candidates, llm)
ctx.temporal_years = parsed["years"]
ctx.missing_years = compute_missing(parsed["years"], kb_covered)   # ← 参照集修正
```

**这就是「否则核心 bug 修不掉」那句注释的原意落点。**

### 3.3 消费：把判定接到提前退出（门 1）

在 `retrieve_kb` 内，**每轮检索之后**执行一次纯代码判定：

```
covered_this_round = {chunk 的 year 元数据} ∪ kb_covered_years     ①
new_chunks = 本轮 chunk_id − 上轮 chunk_id                        ②

if (required_years − covered) 非空 and 已检索 ≥ 1 轮:
    → 标记 ctx.retrieval_exhausted = True
elif 本轮 chunk_id == 上轮 chunk_id (完全重合):
    → 标记 ctx.retrieval_exhausted = True
```

**门 1 的动作（两级，按侵入性从小到大）：**

**级 1（必做）— 工具返回值注入事实 + 选项**

`retrieve_kb` 的返回文本追加：

```
[检索提示] 本轮结果与上一次相同，且知识库不含 2023/2024/2025 的东软年报数据。
继续换措辞重复检索不会获得新内容。你的选项：
 ① 若该问题需要库外信息（如"最新""今年"）→ 改用 search_web
 ② 若该问题属于企业内部数据 → 直接告知用户库中无此数据，不要联网
```

这是**事实注入 + 选项提示**，不是硬指令——保留模型的自主选择（符合 `defensive-patterns` 的"软引导"风格）。

**级 2（可选）— 硬拦截重复检索**

`retrieval_exhausted` 置位后，后续 `retrieve_kb` 调用**不再执行检索**，直接返回同一段提示。硬保证不再浪费检索开销与上下文。

### 3.4 门 2：停之后往哪走（模型判）

门 1 只回答"能不能再搜"，**不回答"该不该联网"**。后者是语义问题，代码判不了：

| 问题类型 | 库里没有时 | 理由 |
|---|---|---|
| 需要实时/外部信息（"最新"、"今年"） | **联网** | 外部确实有 |
| **企业内部数据**（"我们公司的"、内部制度） | **告知用户** | ⚠️ **联网也查不到内部数据，联网是错的** |
| 通用知识 | 直接答或联网 | — |

**现有机制的复用**：`regen_decision.decide_missing_web` + `_ask_web_confirm` 已经实现了「询问用户是否联网 → 记住意愿 → 决策 regen」的正确形态。本提案**不改它**，只把它的**触发时点提前**：

- 现状：`missing` 依赖答案 → 只能在循环末尾触发
- 改后：`retrieval_exhausted` 在检索阶段置位 → **可在循环内触发**（走同一套询问与决策逻辑）

即：**把已验证的决策机制从一个时点搬到另一个时点**，不新增机制。

### 3.5 触发时点的数据依据

trace 里 `reretrieve` 信号在轮 2/3/4 各触发一次（`call_seq=2/3/4`），说明**信号系统已正确识别缺陷**。本提案不再引入新的计数，直接消费该时点。

**保守起步（先严后松）**：

| 参数 | 起步值 | 依据 |
|---|---|---|
| 结果集重合判定 | **完全重合（重合度 = 1.0）** | 100% 重合 = 无新信息，无误判风险 |
| KB 覆盖缺失判定 | 要求年份 ⊄ KB 覆盖 | 直接集合差 |
| 触发时点 | 第 1 轮检索后即可（判据 ①） | 判据 ① 不依赖多轮比较 |
| 重试上限 | 判据 ② 不成立时允许共 **2** 轮 | 比现状 5 轮收紧 |

待积累 trace 后，再看"重合度 0.8~1.0 之间的轮次是否真的捞到过新内容"决定是否放宽。

## 4. 影响面（预计）

| 文件 | 改动 |
|---|---|
| `src/rag/temporal.py` | 拆出 `derive_kb_covered_years`；`derive_candidate_years` 复用它 |
| `src/agents/tools/rag_tools.py` | 修 `compute_missing` 参照集；加每轮判定与 `retrieval_exhausted` 置位；返回值追加提示文本 |
| `src/infra/llm/request_context.py` | 新增 `retrieval_exhausted: bool`、`retrieval_round_chunk_ids: list[set[str]]` |
| `src/agents/graph/verify/regen_decision.py` | 让 `retrieval_exhausted` 也能触发既有询问路径（不新增逻辑） |
| `src/config/const.py` | 提示文案（入 `SSEInteractionTexts` 或新常量） |
| `tests/` | 补"判据 ① 命中即停"、"判据 ② 完全重合即停"、"`missing_years` 参照集修正"三个用例 |

**分层合规**：改动集中在 `rag/`、`agents/tools/`、`infra/`（RequestContext），不触碰 `api/`，无层间调用违规。

## 5. 验证方案

1. **单测**：构造 `kb_covered_years=[2024,2025]` + `required=[2023,2024,2025]` → `missing_years=[2023]`（缺陷 1 的回归）
2. **单测**：两轮检索返回相同 chunk_id → `retrieval_exhausted=True` 且第 3 轮不执行检索
3. **trace 重放**：用 `cli/replay_trace`（或同 query 重跑）对比 `retrieve_kb` 调用次数——期望从 5 次降到 ≤2 次
4. **端到端**：同 session 重问该问题，期望：第 1~2 轮后即询问用户是否联网，总 LLM 调用从 10 次降到 ≤7 次
5. **不回归**：跑全量 `pytest tests/ -v`，确认 `tmp/test_rag_tools.py` 的时间解析用例仍通过（注意该用例的 mock 需同步为可达状态）

## 6. 风险与取舍

| 风险 | 应对 |
|---|---|
| **判据 ① 误判**：KB 元数据 year 字段缺失/不准（如某文档没抽到 year）→ 误判"库里没有" | 元数据缺失的文档不参与覆盖计算，退回判据 ② ；并在日志记录"覆盖年份来源"便于排查 |
| **判据 ② 误判**：不同 query 恰好返回相同 chunk（但其他 query 本可拿到别的） | 起步用"完全重合"最严判据；且判据 ① 优先，只有 ① 不成立时才看 ② |
| **提示文案被模型忽略** | 级 2 硬拦截兜底；另外可观测：埋 `retrieval_exhausted` 后的 `retrieve_kb` 调用次数（期望 0） |
| **提前止损后答案质量下降**（少拿了本可拿到的内容） | 止损只在判据命中时触发；判据 ① 要求"必需年份缺失"（`required_years` 来自用户问题的时间词解析），不是"条数少" |
| **`RETRIEVAL_MAX_PER_DOC=1` 的副作用**（每轮掺入 1 条无关文档） | **本提案不处理**——属独立的上下文纯度问题，需单独立项（见 §7） |

## 7. 本提案未覆盖（需独立立项）

1. **去重上限导致上下文掺入无关文档**：`RETRIEVAL_MAX_PER_DOC=1` 保证多样性，但在"库里只有 2 份文档且其中一份无关"时，会让每轮上下文含 50% 噪音。这是**上下文纯度**问题，与止损判定正交。
2. **rerank 后 8→2 的落差**：本 trace 中 `hybrid=8 → rerank=2`，需确认是 rerank 正常筛选还是阈值过严（`retrieval.py` 注释称"不应用绝对分数阈值过滤"，需复核）。
3. **KD 库规模与用户预期的落差**：测试库只含 2 份文档，但 UI 未向用户暴露"库里有多少文档/覆盖哪些年份"。可考虑在候选断层时把 KB 覆盖画像一并回给用户。
4. **`to_web` 信号的消费**：与 `reretrieve` 同类，同为"采集了但未消费"。
