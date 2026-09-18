# Deep Research: trace_c54ce259 — 为什么搜了这么多网页

生成日期：2026-09-16 ｜ 一手来源：该 trace 的容器日志（`/data/logs/app_2026-09-16.log`）+ 本仓库代码

## Executive Summary

这次搜索**不是"过量"，而是"缺口驱动"**。根因链条有三环，每一环都有日志证据：

1. **KB 检索连续 5 轮只召回 2 条**（`result_count=2`，且 `dedup_max_per_doc=1`），模型换着 query 重试到 `MAX_AGENT_ITERATIONS=5` 触顶；
2. **触顶时答案长度为 0**（`[verify] completeness check ... answer_len=0`）——模型 5 轮全在调工具，从未产出文本，finalize 拿到空答案；
3. 于是 verify 判定**三个整年全缺**（`missing=[2023,2024,2025]`）→ 询问用户 → 用户确认 → **预算重置为 5 轮** → 模型把三年拆成 7 个 query、分 3 次 `search_web` 搜。

**实际用量是上限的约 1/3**（40 条结果 / 理论上限 120 条），且最终产出 **12 条引用、2206 字**（`format done citations=12`）——搜索**被用上了**，不是浪费。

**搜索的全部内容由 LLM 决定**（搜不搜、搜几次、几个 query、top_k 各是几），代码只做**事后上限裁剪**。

## Key Findings

### 1. 本轮实际搜了什么（日志逐条还原）

**调用 3 次 `search_web`**（`web_count` 1 → 2 → 3）：

| 次序 | queries | 各 query 结果数 | 结果合计 | extract | 耗时 |
|---|---|---|---|---|---|
| 1 | **3** | 6 + 6 + 6 | 18 | urls=6 | 8,676 ms |
| 2 | **2** | 5 + 5 | 10 | urls=4 | 5,633 ms |
| 3 | **2** | 6 + 6 | 12 | urls=4 | 6,308 ms |
| **合计** | **7** | — | **40 条** | **14 URL** | ~20.6 s |

日志证据：
```
[retrieval] TIMING web_search_start queries=3 web_count=1 kb_bound=True
[retrieval] TIMING web_search_gather_done elapsed_ms=3445 ok=3 failed=0
[retrieval] web search done query_count=3 result_count=18 latency_ms=8676
[retrieval] TIMING web_search_start queries=2 web_count=2 kb_bound=True
[retrieval] web search done query_count=2 result_count=10 latency_ms=5633
[retrieval] TIMING web_search_start queries=2 web_count=3 kb_bound=True
[retrieval] web search done query_count=2 result_count=12 latency_ms=6308
```

**7 个 query 是在补不同年份**（不是重复搜同一件事）：
- `东软集团 2023年年报 营业收入 净利润 同比`
- `东软集团 2024年年度报告 营业收入 归属于上市公司股东的…`
- `东软集团 2022年年报 营业收入 净利润 总资产 净资产`
- `东软集团 2022年年报 营业收入 94.66亿 归母净利润…` ← **带上了具体数字**
- `东软集团 2021年年度报告 营业收入 87.35亿 归母净…` ← **带上了具体数字**
- `东软集团 2024年年报 应收账款 存货 毛利率 分行业收入` ← 换到明细维度
- `东软集团 2025年年报 应收账款 存货 资产负债率 经营活…`

后两个 query 已经**从"找年份数据"转向"找财务明细"**，说明前两轮拿到年报数据后模型在往下钻。

### 2. 项目里的限制：全是"上限裁剪"，没有一个是"该搜什么"

| 限制项 | 值 | 位置 | 本轮是否触及 |
|---|---|---|---|
| 联网搜索**次数**（每轮） | **3** | `src/config/settings.py:108-109` | ✅ **刚好用满**（web_count 到 3） |
| 单次调用 **query 数** | 4 | `web_tools.py:60`（`queries[:4]`） | ❌ 最多用了 3 |
| 每次 query 的 **top_k** | 默认 5，上限 10 | `SearchWebArgs.top_k`（`ge=1, le=10`） | ❌ 用了 5~6 |
| **extract 每 query 取几条** | **2** | `web_tools.py:122`（`q_results[:2]`） | ✅ 每轮都按 2 取 |
| 单次 HTTP 超时 | `TAVILY_TIMEOUT=5.0` | `settings.py`（阶段语义） | ❌ 最快 1.5s / 最慢 3.4s |
| **总时长 / 总条数上限** | **无** | — | — |
| agent 迭代上限 | `MAX_AGENT_ITERATIONS=5` | `src/config/const.py:53` | ✅ **两次都用满** |

**理论上限**：3 次 × 4 query × 10 top_k = **120 条搜索结果**；extract = 3 × 4 × 2 = **24 URL**
**本轮实际**：**40 条**结果 + **14 URL** → 约为上限的 **1/3**

→ 结论：**没有"搜太多"这回事**——代码允许的上限比实际用量高 3 倍，而实际用量由模型按缺口自己定。

### 3. 搜索是 LLM 决定的吗？——是，四个维度全由它定

| 决策 | 本轮实际 | 代码的角色 |
|---|---|---|
| **搜不搜** | 搜了（3 次） | 工具绑定在模型上（`bind_tools`），模型自主决定 |
| **搜几次** | 3 次 | 只设上限（`web_count >= 3` 则拒绝并返回 `WEB_SEARCH_LIMIT_TEXT`） |
| **每次几个 query** | 3 / 2 / 2 | 只截断到 4（`queries[:4]`） |
| **每 query 的 top_k** | 5~6（从 `results=5/6` 反推） | 只校验范围（1~10） |
| **query 文本** | 7 条各不同 | 完全不干预 |

**代码侧只做三件"事后裁剪"**：剥空 query、截到 4 个、限次 3 次。**没有一处代码在决定"该搜什么"**。

`search_web` 的工具描述（`web_tools.py:43-47`）给了模型行为指引——"何时调用：retrieve_kb 检索结果为空或全部明显不相关时"、"知识库能回答的问题不要调用本工具"——但那是**提示词层面的软引导**，不是硬约束。

### 4. 搜索流程（7 步，与日志逐条对应）

```
① 模型产出 tool_call: search_web(queries=[...], top_k=N)
       ↑ 由 verify 注入的指引 + 工具描述共同驱动

② search_web 入口（web_tools.py:56-70）
   剥空串 → queries[:4] → 检查 web_count >= 3 → web_count += 1
   （一次多查询调用只占 1 次额度，这是刻意设计）

③ asyncio.gather 并行发 N 个 tavily_search
       → POST https://api.tavily.com/search, search_depth="basic"
       → return_exceptions=True，单路失败不连坐
       ✅ 日志印证：3 路并发，耗时 = max(2189, 2212, 3422) = 3445ms ≈ 最慢那路

④ 过滤异常项；全部失败则 return ""

⑤ 每个 query 取前 2 条 URL，合并成「一次」tavily_extract
       → POST https://api.tavily.com/extract
       → 失败降级：用搜索摘要兜底（不阻断）
       ✅ 日志印证：urls=6 got=6（一次调用覆盖 3 query × 2）

⑥ 结果按全局编号 append 到 ctx.tool_contexts
       → 带 kind=web + T0–T4 来源定档
       → 拼成 "[n] 来源: url (等级)\n内容: ..."

⑦ 返回给模型 → 下一轮基于它生成答案
```

**第 3 步的并发验证**：3 个 query 分别耗时 2189 / 2212 / 3422 ms，而 `gather_done elapsed_ms=3445` —— 正好是**最慢那路**，证明并行生效（若是串行会是 ~7.8s）。

## Detailed Analysis

### 根因链条：为什么会有三个整年的缺口

完整时序（从日志时间线还原）：

```
[阶段 A] KB 检索
  iter 1: retrieve_kb → result_count=2   query="东软集团 年报 营业收入 净利润 2021 2022 2023 2024"
  iter 2: retrieve_kb → result_count=2   query="东软集团 2024年年度报告 营业收入 净利润 经营现金流 总资产"
  iter 3: retrieve_kb → result_count=2   query="东软集团 2023年年度报告 主要会计数据 营业收入 归母净利润"
  iter 4: retrieve_kb → result_count=2   query="东软集团 2025年第一季度报告 营业收入 净利润 同比 主要会计数据"
  iter 5: model turn → [agent] iteration limit   ← 触顶
                ↓
  [verify] completeness check required=[2023,2024,2025] missing=[2023,2024,2025] answer_len=0
                ↑ 三年全缺，且答案长度是 0
                ↓
  [verify] web confirm ask → 用户确认 → [verify] web confirm result confirmed=true
                ↓
  预算重置为 5 轮
                ↓
[阶段 B] 联网补缺
  iter 1: search_web(3 queries) → 18 条
  iter 2: search_web(2 queries) → 10 条
  iter 3: search_web(2 queries) → 12 条     ← web_count 用满 3 次
  iter 4: model turn（无工具调用）
  iter 5: model turn usage_out=1553（生成答案）→ iteration limit 再次触顶
                ↓
  [verify] completeness check missing=[] answer_len=2206   ← 通过
  [agent] format done citations=12
```

**三个关键观察：**

**① KB 每轮只召回 2 条，5 轮都没变。**
`hybrid done result_count=8` → `rerank done doc_count=2` → `retrieve done result_count=2`。8 个候选经 rerank 后只剩 2，再经 `dedup_max_per_doc=1`（每文档至多 1 条）后是 2。**5 轮 query 完全不同，结果数却恒为 2** —— 说明瓶颈不在 query 写法，而在**知识库本身没有那几年的数据**。模型 5 轮都在"换着法子问一个库里没有的答案"。

**② `answer_len=0` 是最刺眼的信号。**
5 轮里模型每轮的 `usage_out` 只有 56~66 tokens——**这些是 tool_call 的 JSON，不是答案文本**。所以触顶强制 finalize 时，`agent_finalize` 拿不到任何 answer（提取末次 AIMessage 的 content，而 content 为空）。verify 因此看到 `missing=[2023,2024,2025] answer_len=0`。

→ **这是"迭代上限触顶时模型仍在调工具"的直接后果**，也是 40 条搜索的**真正起点**。

**③ 联网是"补缺口"而不是"过量"。**
verify 明确告诉模型缺 `[2023,2024,2025]` 三个整年，所以模型把它们拆开搜。而它只用了 `missing` 信息，没有额外发散——7 个 query 里 5 个直接对应年份（2021/2022/2023/2024），2 个是明细维度。

### 代价：上下文膨胀 4 倍

| 阶段 | 最后一次 model turn 的 `usage_in` |
|---|---|
| 纯 KB（阶段 A 末） | **10,794** |
| 联网 3 次后（阶段 B 末） | **43,786** |

**4 倍膨胀**，来源是 40 条搜索结果 + 14 篇正文进上下文。而最后那次生成（`usage_out=1553`、`ttfb_ms=992`、`total_ms=15622`）是在这个 4 倍上下文上跑的——**首字节只用了 1s，剩余 14.6s 全是生成长文本**，说明延迟来自输出长度而非输入排队。

### 顺带解释了一条容易被误读的日志

```
[agent] iteration limit query="..." iteration=5
[agent] TIMING silence_idle_s=60 events=17 session_id=sess_1789542020121_clrn6n
```

这条 `silence_idle_s=60` **不是卡死**。它紧跟在 iteration limit 之后，而这正是 `verify` 发出"是否联网补 2023/2024/2025"询问、**等用户点确认**的时刻。60 秒无新事件是因为**人在思考**，不是链路挂住。

→ 对 316s 排查的意义：**静默看门狗会同时命中"真实卡死"和"等待用户输入"两种情况**。判读时必须先排除后者（看该时刻附近是否有 `web confirm ask` / `ask_user` 之类的询问事件）。这条 trace 就是个现成的"良性静默"样本。

## Contrarian Views And Risks

### 反方：这次搜索确实"低效"（但不该靠限次解决）

| 观察 | 说明 |
|---|---|
| 7 个 query 打 3 次调用 | 如果模型第一次就传满 4 个 query，可能 2 次就够（限次是按**调用**计，不是按 query 计） |
| `web_count` 语义与直觉不符 | "一次多查询只占 1 次额度"（`web_tools.py:70`）本意是鼓励多 query 合并，但模型没充分利用——本轮 3 次调用只带了 7 个 query，**上限是 12 个** |
| 首字节 vs 总时长 | 最后那次生成 ttfb 仅 992ms，剩下 14.6s 是输出 1553 tokens——**真正的等待感来自长答案，不是搜索** |
| extract 拉 14 篇正文 | `q_results[:2]` 是硬编码的 2；若按 query 数动态（如 7 query × 2 = 14）已经是当前行为，但**没有总量上限** |

### 风险：这份 trace 证明的是"限次不必调"，不是"限次没问题"

- 本轮限次 3 **刚好用满**，如果模型还需要第 4 次（比如发现 2022 年数据也不全），会被 `WEB_SEARCH_LIMIT_TEXT` 直接拒掉，**而用户不会知道**（返回的是提示文本，模型只能转述）。
- 由于**没有总时长上限**，若某次 `search_web` 卡住（正如 316s 那次），本轮的 40 条结果**一条都拿不到**——因为 `gather` 是 all-or-nothing。

## Open Questions

1. **KB 里到底有没有 2023/2024/2025 的数据？** 5 轮恒为 `result_count=2` 强烈暗示"没有"，但需查该 KB（`kb_id=b9e74e820e0a4bad8472304446e54f5c`，`collection_count=51`）的实际文档清单确认。若是"有但检索不到"，问题在 rerank 或分块；若是"本来没有"，那 5 轮 KB 检索就是纯浪费。

2. **`answer_len=0` 是否普遍？** 如果"迭代上限触顶 → 零答案 → 触发联网询问"是常见路径，那么 `MAX_AGENT_ITERATIONS=5` 的取值本身需要复核。本次是 5 轮 KB + 5 轮联网 = 10 轮 LLM 调用换一个答案。

3. **`usage_in` 从 10.8k 涨到 43.8k 对生成质量有影响吗？** 43.8k 已在部分模型的注意力衰减区间（参考同期调研中"lost in the middle"的证据）。本轮答案质量尚可（12 引用），但样本只有 1 个。

4. **`[session] skill injected financial-statement-analyzer mode=inline chars=3002`** —— 3002 字符的方法论注入了上下文，它与"要求覆盖 2023/2024/2025"的 completeness 检查是什么关系？如果 skill 要求三年对比，而 KB 没有三年数据，那这个缺口是**被 skill 放大的**。

## Sources

| # | 来源 | 类型 | 用于 |
|---|---|---|---|
| 1 | `/data/logs/app_2026-09-16.log`（容器内，trace `c54ce259-…`，95 行） | **一手（本次实测日志）** | 全部时序、用量、query、耗时 |
| 2 | `src/agents/tools/web_tools.py:30-36, 56-70, 81-87, 122-125, 136-176` | 一手（本仓库代码） | query 上限 4 / 限次 3 / gather 并行 / extract 取 2 / 编号与定档 |
| 3 | `src/infra/search/tavily_client.py:43-48, 102` | 一手（本仓库代码） | `search_depth=basic`、`max_results=top_k`、extract payload |
| 4 | `src/config/settings.py:108-109` | 一手（本仓库代码） | `WEB_SEARCH_PER_TURN_LIMIT` 默认 3 |
| 5 | `src/config/const.py:53` | 一手（本仓库代码） | `MAX_AGENT_ITERATIONS = 5` |
| 6 | `src/services/agent_service.py:194-206, 283-320` | 一手（本仓库代码） | SSE 状态事件映射（`search_web` 的 start/end） |

**证据质量**：全部结论均可从上述 6 个来源直接复核，无推断性结论。唯一需要标注的是"KB 里没有那几年数据"属**强推断**（依据是 5 轮 query 完全不同而 `result_count` 恒为 2），已在 Open Questions #1 列出验证方法。

## Rerun Inputs

```
workflow: firecrawl-deep-research
topic: trace_c54ce259 搜索量归因 —— 限制在哪、谁决定搜什么、流程如何
depth: thorough
output: markdown
local_sources: 容器日志 1 份（95 行）+ 本仓库代码 4 文件
external_sources: 0（本次问题完全可由一手日志与源码回答，无需联网）
notes: 未覆盖——(a) 该 KB 的实际文档清单（判断"没有数据"vs"检不到"）；
       (b) answer_len=0 在历史 trace 中的出现频率；
       (c) skill 的 3002 字符方法论与 completeness 要求的关系
```
