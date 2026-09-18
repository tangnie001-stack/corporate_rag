# Deep Research: 前置意图门（classify_node）要不要恢复

生成日期：2026-09-15 ｜ 深度：Thorough ｜ 采集：10 查询 + 5 深抓

## Executive Summary

调研的核心发现是：**「要不要前置门」这个提法本身是模糊的**——它至少混杂了三个不同的问题（工具命名空间收窄 / 简单问题省延迟 / 按意图换 prompt），三者结论相反。业界证据两侧都很强，但适用条件清晰可辨。

对你的具体场景（约 6 个工具、企业知识问答、流量以需要检索的问题为主），证据**支持你删除 classify_node 的判断**，但理由需要修正：不是"前置分类不划算"，而是**你的场景落在两个已知的失效区间里**——工具数量远低于分类层见效的阈值，且级联收益取决于"简单请求占比"这个你尚未测过的量。

同时必须指出一个我们此前讨论中的判断错误：**你的架构本质上是 cascade（级联），不是 upfront routing（前置路由）**。文献明确论证 cascades 的信息流方向更优（用真实输出而非预测失败作证据），这从理论上支持了你的直觉，但也意味着"给 agent 加一条规则短路"会把这个优势丢掉。

最后，"能力倒挂"这个假设**在学术上有对应物**（non-monotonic model quality / signed treatment effect），但现有实证针对的是"弱模型回答 + 强模型纠正"的形态，**不是**"弱模型分类 + 强模型执行"。你的假设方向合理，但缺乏直接实证。

## Key Findings

### 1. 前置门的必要性首先取决于**工具数量**，而非模型强弱

tianpan.co 给出了量化曲线（[来源](https://tianpan.co/blog/2026/04/16/intent-classification-agent-routers)）：

| 工具数 | LLM 自主选择工具的准确率 |
|---|---|
| 50 | 94% |
| 200 | **64%** |
| 417 | 20% |
| 741 | **13.6%**（与随机猜测无法区分）|

**实践启发式（原文）**：
> 少于 15 个工具，LLM function calling 够用。15–50 个工具，加一个 embedding router。多于 50 个，需要微调分类器。多于 100 个，分类层**不可谈判**。

**对你的适用性**：你的工具是 `retrieve_kb` / `search_web` / `ask_user` / `delegate_task` + task 类，**约 5–6 个**。按这个启发式，你在"够用"区间内，**没有任何工具规模压力**。

### 2. "模型变强就不需要前置门"——这个说法有一条明确的反例

tianpan 把它归因于**物理约束**而非模型缺陷：

> 退化（准确率随工具数下降）的结构性原因，是研究者所称的 "context rot"。Transformer 注意力随上下文长度呈平方增长。**跨 18 个前沿模型的研究发现，每一个都会随输入变长而退化**，位于上下文中点（50%）的工具被选择得明显更不准确——即便正确工具就在上下文里。
>
> "lost in the middle" 不是模型 bug。**它是物理约束。你无法通过更用力地提示、或升级模型版本来解决它。** 你只能通过一开始就不加载无关工具来解决。

**这修正了我们之前的一处讨论**：我此前说前置门的价值会"随模型变强而衰减"，tianpan 的证据表明**在工具规模压力下不会**。所以正确的表述是：**前置门的价值取决于它 gate 什么**——gate 工具命名空间（对抗物理约束，不衰减）与 gate 简单问题（对抗延迟，可能衰减）是两件事。

### 3. 你的架构是 **cascade**，不是 **upfront routing**——这个区分是本次调研最有价值的发现

mbrenndoerfer 指出两者的**信息流方向**根本不同（[来源](https://mbrenndoerfer.com/writing/model-routing-selection-ab-testing-cascades-strategies)）：

> **upfront routing classifier 在任何响应生成之前就做决定。cascade 先生成响应，再判断这个响应是否足够好。** 这让 cascade 能把**实际的模型输出**——包括它的置信度与不确定性——作为升级决策的证据。**你不是在预测模型会不会失败，你是在观察它是否失败，并据此行动。**

**对照你的系统**：
- 你的 agent 第一轮调用（bind_tools）**就是** cascade 的第一次生成——它决定直答还是调工具
- classify_node（upfront）**跳过**这次生成，改为在信息更少的时点做预测
- 按上述论证，**你删掉它是在正确的方向上**——你保留了"用真实输出作证据"的能力

这条证据比"性价比"更有力，因为它不依赖流量分布假设。

### 4. 「能力倒挂」在学术上有对应物，但形态与你的不同

arXiv 2609.07786《Signed Rescue Routing: Harm-Aware Cascades》的章节结构本身就是论证（[来源](https://arxiv.org/html/2609.07786v1)）：

- **2 Why Error Prediction Is Not Enough**
  - **Correlated errors waste budget.**
  - **Non-monotonic model quality creates harm.** ← 强模型并非总是更好
  - **The router predicts a treatment effect.**
- 摘要中的关键句：**"升级只在强模型能纠正弱模型时有用；当强模型用一个更差的答案替换掉正确的答案时，升级是有害的。"**

**这就是"能力倒挂"的学术表述**：`non-monotonic model quality`——升级动作可能带来**负收益**，所以路由器应该预测**带符号的增益**（signed treatment effect），而不是预测"会不会出错"。

**重要的边界**：这篇论文研究的是"弱模型回答 → 强模型纠正"，**不是**"弱模型分类 → 强模型执行"。你的假设（用弱模型给强模型做前置决策属于能力倒挂）**方向合理、机制同源，但没有直接实证**。这是一个诚实的缺口。

### 5. 规则门（正则 / 短查询阈值）有**系统性脆性**，不只是"不够聪明"

这直接关系到我们讨论过的 `_GREETING_PATTERNS` + `_SHORT_QUERY_THRESHOLD` 零成本方案。mbrenndoerfer 的评估（同来源）：

> 规则路由易于实现、检查、调试……每条路由决策都能追溯到具体条件，这在受监管行业或调试质量回退时很重要。
>
> **缺点是脆性。规则无法捕捉"什么让一个请求变难"的全部复杂性。一个很短的请求可能需要复杂推理。一个很长的请求可能只是一份结构重复的简单文档。** 由长度、关键词这类代理特征出发的规则会**系统性地误判**这些情形。随着产品演进，你会不断累积例外、特例和绕行方案，规则集越来越脆。**六个月前简单的请求，今天可能因为用户群变化而需要大模型。**
>
> **规则路由最适合作为起点，而不是终点。**

**关键句「A short request can require sophisticated reasoning」直接击中 `_SHORT_QUERY_THRESHOLD`**——短查询被判定为"简单"是一个**代理特征误判**。你的短查询可能是"2024 年营收？"（需要检索）这类。

### 6. 级联在**高难度分布**下是纯开销

mbrenndoerfer 列出级联失效的两个条件：

> - 当小模型**非常慢**时，永远先跑小模型的延迟代价可能不可接受。
> - **当几乎所有请求都需要大模型时（高内在难度分布），级联会浪费"每个请求都先跑小模型"的成本。如果 80% 的请求都会升级，那么小模型的运行是纯开销。**

**这直接决定你的答案，而答案是数据依赖的**：你的流量里"能直答的简单问题"占比多少？
- 若占比高（如 >30%）→ 前置门有正收益
- 若占比低（如 <20%）→ 前置门是纯开销

**你目前无法回答这个问题**，因为 classify_node 已删除、没有对照数据。但**可以用已有的行为信号近似测量**（见 Open Questions）。

### 7. Anthropic 官方支持 Routing，但给了明确前提条件

官方《Building effective agents》把 Routing 列为推荐 workflow 模式之一（[来源](https://www.anthropic.com/engineering/building-effective-agents)）：

> Routing 适用于**复杂任务中存在明确分类、且这些分类更适合分开处理**的场景，并且**分类可以被准确完成**——由 LLM 或更传统的分类模型/算法。

**它的两个条件都很关键**：
- 「明确分类」→ 你的"闲聊 vs 检索"是否真的清晰可辨？（"你好，顺便问下 2024 营收"这种混合意图算什么？）
- 「分类可以被准确完成」→ 这是**必要条件而非保证**，与 tianpan 的"误分类会级联"形成闭环

同时 Anthropic 同一篇文章的框架建议仍然成立（我们此前已引）：**"don't hesitate to reduce abstraction layers and build with basic components as you move to production"**。

### 8. 误分类的代价是级联式的

q10 的检索结果一致指向同一结论（[来源](https://medium.com/@mr.murga/enhancing-intent-classification-and-error-handling-in-agentic-llm-applications-df2917d0a3cc)）：

> **错误传播**：一次不正确的分类（例如把 "payroll" 标成 "benefits"）会误导后续的 agent，**导致级联失败**。

tianpan 给出了量化：**"agent 工作流中一次错误路由可以级联成五六次 API 往返才能恢复。"** 而你的 verify 节点 + regen 预算机制正是这类恢复成本。

### 9. 分类器实际 gate 的第三件事：**system prompt 选择**

tianpan 明确列出分类器输出 gate 的三件事：

> - **Tool namespace filtering**：只加载相关工具
> - **System prompt selection**：LLM 收到的是领域专家 system prompt 而非通用 agent prompt。**领域预置提升领域推理准确率。**
> - **Agent delegation**：在多 agent 架构中路由到专门的子 agent（各自的工具集、prompt、记忆范围）

**这是"换 prompt"的业界确认**——也正是我们此前判断"agent 无法自理"的那一项。所以如果你的 classify 目的包含按意图换人设，它**不可替代**；如果只为了跳检索，它可替代。

## Detailed Analysis

### 三个被混为一谈的问题

调研中最重要的收获是：**「前置门值不值得」在三件事上是三个相反的答案**。

| 它想 gate 什么 | 业界证据 | 你的场景适用性 |
|---|---|---|
| **① 工具命名空间收窄** | 强正面（741 工具 → 13.6%，117x token 削减，GeckOpt 实测 24.6% token 降 / <1% 精度损） | ❌ **不适用**——你只有 ~6 个工具，在"够用"区间 |
| **② 简单问题省延迟/成本** | 有条件的正面（BoundaryRouter -60.6% 时间 / -11.5% 相对精度） | ⚠️ **取决于你的流量分布**，未测 |
| **③ 按意图换 system prompt** | 正面且不可替代（tianpan 列为 gate 三事之一） | ⚠️ **只有你确实需要时才成立** |

**结论**：你当前的工具规模让 ① 归零；② 需要测量；③ 是唯一的架构性理由。

### 你的 cascade 结构其实已经是"更优解的一半"

把 mbrenndoerfer 的信息流论证、arXiv 的 signed treatment effect、以及 tianpan 的"分类器不替代推理"三条合起来看，得到的图景是：

- **upfront 分类** = 在信息最少的时点做预测（只看原始 query）
- **cascade** = 在信息最多的时点做判断（看过模型的第一轮输出）
- 文献立场：**后者在机制上更优**，因为它用证据替代预测

你的 `agent ↔ tools` 循环 + `verify` 后置校验，**恰好是 cascade 形态**：第一轮生成即决策，verify 根据真实产物决定是否 regen。

**所以"恢复 classify_node"在架构上是一次倒退**——你会在信息最少的位置重新插入一个预测点。这比"多一次 LLM 调用"是强得多的反对理由。

### Miessler 的 WHAT/HOW 判据：一个可以直接使用的决策工具

Daniel Miessler 对"harness 到底有没有价值"的争论给出了一个二分（[来源](https://danielmiessler.com/blog/the-answer-to-the-harness-question)）——起因是 a16z 的 Martin Casado 公开表示他在三个信念间摇摆（"harness 越少越好，模型才是魔法" / "后训练会让模型厂商赢" / "harness 有独立价值"）：

> 为什么这个问题看起来无解，是因为我们把 harness 当成了一个东西。**它实际上是两个。** 每个 harness 都不同程度地承载 WHAT 和 HOW——关于你想要什么的上下文，以及如何得到它的指令。而这**两半的老化方向相反**。
>
> **HOW 那一半会腐烂。** 这是 Sutton 的 Bitter Lesson 在你的配置文件里上演：**模型越聪明，你的分步指令相比之下就越蠢。** 如果你的 harness 主要是 HOW，那么"harness 越少越好"是对的。
>
> **WHAT 那一半会升值。** 你是谁、你在做什么、你想达成什么、什么对你是好的。**更聪明的模型能用这些上下文做更多事，而不是更少。**
>
> 所以第一个和第三个信念都对——它们只是关于 harness 的不同两半。

**套用到你的问题**：

| 组件 | WHAT 还是 HOW | 老化方向 |
|---|---|---|
| 意图分类门（决定"这类输入怎么处理"） | **HOW** | **腐烂** |
| `verify` 完整性校验（"哪年份缺失要说"） | WHAT（你的质量定义） | 升值 |
| 来源等级 T0–T4 | WHAT（你的可信度标准） | 升值 |
| 时序约束（"候选年份 = KB 元数据 ∪ 近 3 年"） | **HOW** | 可能腐烂 |
| 智能体预设人设 | WHAT | 升值 |

**这是一把可以直接用的尺子**：前置分类门落在 HOW 那一侧，属于"模型变强后会被比下去的"部分。而你的 verify / 来源等级 / 人设属于 WHAT，会随模型变强而升值。

（Miessler 原文还提到他为此写了两篇前作：Intent Engineering、Good and Bad Harness Engineering——同一判据的展开。）

### 生产实践：三层漏斗（供对照，不是建议）

dev.to 的工业实践描述了一个三级结构，并特别指出"用大模型直接做意图分类**不是生产系统的做法**"（[来源](https://dev.to/wonderlab/agent-series-5-intent-recognition-and-routing-making-agents-actually-understand-users-3174)）：

| 层 | 延迟 | 成本 | 流量占比 | 用途 |
|---|---|---|---|---|
| 规则路由（关键词 + 正则 + FSM） | <1ms | 极低 | ~5% | 固定命令、快捷动作 |
| 微调 SLM（5B/7B） | 10~50ms | 低 | ~90% | 常规意图分类 |
| 大模型 | 100~500ms | 高 | ~5% | 长尾、复杂、边界情况 |

另有 **OOD Rejection 层**——过滤服务范围外的请求（原文例子："对购物助手说'给我写首诗'"）。**这一层值得你单独注意**：如果你的 `abstention`（拒答）承担的是"检索无达标 context 就拒答"，那它与 OOD rejection 是**不同职责**——OOD 是"这个问题不属于我的服务范围"，abstention 是"这个问题属于范围但我没检索到"。两者可能都缺一不可。

tianpan 的级联版本还给出了**置信度阈值策略**：>0.8 自动路由；0.5–0.8 路由但标记复核；<0.5 升级或转人工。

**注意这三个生产方案都比"一次 LLM 分类"复杂**，也都比你的 `_GREETING_PATTERNS` 正则复杂。这说明业界认为这件事**值得投入**——但前提是它们有那个工具规模与流量。

## Contrarian Views And Risks

### 反对我上述结论的证据（前置门一侧）

必须诚实呈现：如果你的工具面增长，结论会反转。

1. **tianpan 的 117x token 削减**：741 工具时每请求 127,315 tokens 开销 → 分类层降到 1,084 tokens。按百万请求/月计，约 379 万美元/年的 API 成本差。
2. **GeckOpt 模式有真实生产验证**：Microsoft Research，100+ GPT-4-Turbo 节点的 Copilot 系统，**24.6% token 削减且精度损失 <1%**。
3. **BoundaryRouter 的实测**：路由 vs 纯 agent，**时间减少 60.6%，相对准确率仅降 11.5%**；vs 纯 LLM 直答，准确率提升 28.6%。
4. **Anthropic 官方立场**：Routing 是推荐模式，不是反模式。
5. **"分类器不替代推理"的澄清**：tianpan 明确反驳了"分类器会削弱模型"的常见误解——分类器只回答"哪个工具命名空间相关"，**LLM 的推理保持完整**。

### 风险清单

| 风险 | 说明 |
|---|---|
| **误分类级联** | 一次错路由 → 五六次 API 往返才能恢复（tianpan）；"payroll 标成 benefits"会误导后续流程（q10） |
| **规则门系统性脆性** | 短查询可能很复杂、长查询可能很简单；规则会稳定误判（mbrenndoerfer） |
| **OOD 缺口** | 若你的 abstention 只覆盖"检索无结果"，则"范围外请求"无人拦截 |
| **未来工具增长** | 若你接入 MCP（P2 路线），工具数可能从 6 涨到 20–30，此时分类层的必要性上升 |
| **不要为了消除一个 LLM 调用而引入规则门** | 规则门的错判率高于 LLM 分类，且错判代价是级联失败 |

### 对"能力倒挂"假设的反驳视角

- tianpan 明确认为分类器**不损害**模型推理能力（"The LLM's reasoning stays intact"）——这是对你假设的直接反对。
- 但也必须承认：他的场景是**收窄工具集**，不是**跳过生成**。在你的场景里 classify_node 会**直接决定不调用工具**，这比"收窄"更激进，因此他的辩护不完全适用于你。

## Open Questions

1. **你的流量里"可直答的简单问题"占比多少？** 这决定级联是正收益还是纯开销（mbrenndoerfer 的 80% 阈值论证）。**无法从现有数据回答**，但可以近似测量：用行为信号统计 `abstain_after_retrieve` / `to_web` / `reretrieve` 中发生在短查询（≤2 字或命中问候正则）上的比例。若占比极低，说明你根本没有需要 short-circuit 的流量。

2. **你的 classify 当初想 gate 的三件事里，哪些是 agent 不能自理的？** 按 tianpan 的清单：工具命名空间（你能自理）、按意图换 prompt（**不能自理**）、委派路由（你能自理——已有 `delegate_task`）。**只有"换 prompt"构成不可替代理由。**

3. **能力倒挂假说在"弱模型分类 + 强模型执行"形态上缺乏直接实证。** 现有文献是"弱模型回答 + 强模型纠正"（arXiv 2609.07786）。要验证你自己的形态，只能自测（A/B：同一批 query，有/无前置门，对比最终答案质量而非延迟）。

4. **混合意图怎么处理？** Anthropic 的前提条件是"存在明确分类"。"你好，顺便问下 2024 营收"既 greeting 又 kb_search。分类器的 OOD/低置信度策略能否覆盖这类？

5. **你的 `abstention` 是否已承担 OOD rejection 职责？** 若只覆盖"范围内检索不到"，则"范围外"是缺口。这独立于 classify_node 的决策。

## Sources

| # | 来源 | 类型 | 用于 |
|---|---|---|---|
| 1 | [Anthropic《Building effective agents》](https://www.anthropic.com/engineering/building-effective-agents) | 官方一手 | Routing 模式的前提条件；框架抽象建议 |
| 2 | [tianpan.co《The Intent Classification Layer Most Agent Routers Skip》](https://tianpan.co/blog/2026/04/16/intent-classification-agent-routers) | 工程博客（最核心） | 工具数量-准确率曲线；context rot 物理约束；级联设计；gate 三事；GeckOpt |
| 3 | [mbrenndoerfer《Model Routing: Selection, A/B Testing, Cascades》](https://mbrenndoerfer.com/writing/model-routing-selection-ab-testing-cascades-strategies) | 技术长文 | **upfront vs cascade 信息流方向**；级联失效条件；规则门脆性 |
| 4 | [arXiv 2609.07786《Signed Rescue Routing: Harm-Aware Cascades》](https://arxiv.org/html/2609.07786v1) | 论文 | **non-monotonic model quality**；升级有害的条件 |
| 5 | [arXiv 2605.07180《Learning Agent Routing From Early Experience》](https://arxiv.org/html/2605.07180v1) | 论文 | BoundaryRouter 实测：-60.6% 时间 / -11.5% 相对精度；agent 比 LLM 慢 ~60× |
| 6 | [dev.to《Agent Series (5): Intent Recognition and Routing》](https://dev.to/wonderlab/agent-series-5-intent-recognition-and-routing-making-agents-actually-understand-users-3174) | 工程实践 | 三层生产漏斗；OOD rejection；数据飞轮 |
| 7 | [Daniel Miessler《The Answer to the Harness Question》](https://danielmiessler.com/blog/the-answer-to-the-harness-question) | 观点（WHAT/HOW 框架） | **HOW 会腐烂 / WHAT 会升值**的判据 |
| 8 | [browser-use《The Bitter Lesson of Agent Harnesses》](https://browser-use.com/posts/bitter-lesson-agent-harnesses) | 观点 | "your helpers are abstractions too" |
| 9 | [Guild.ai《Query Routing (AI)》](https://www.guild.ai/glossary/query-routing-ai) | 词汇表 | 生产部署中 query routing 把准确率从 58% 提到 83% |
| 10 | [gist: Intent Recognition and Auto-Routing in Multi-Agent Systems](https://gist.github.com/mkbctrl/a35764e99fe0c8e8c00b2358f55cd7fa) | 笔记 | LLM 意图识别的 cons（慢、贵、易误分类）；专用路由 agent |
| 11 | [Medium: Enhancing Intent Classification and Error Handling](https://medium.com/@mr.murga/enhancing-intent-classification-and-error-handling-in-agentic-llm-applications-df2917d0a3cc) | 工程博客 | 误分类导致级联失败 |
| 12 | [AWS Builder《RAG from Basics to Advanced (Part 6): Query Routing》](https://builder.aws.com/content/3J5pM6hM9CDbtL5jEGZ3xxnbkTE/rag-from-basics-to-advanced-part-6-query-routing-multi-hop-retrieval-and-agentic-rag) | 厂商文档 | query routing 决定"是否检索"；agentic RAG 让模型动态决定两者 |
| 13 | [x/buckeyevn: Why Most Agent Harnesses Are Not Bitter Lesson Pilled](https://x.com/buckeyevn/status/2014171253045960803) | 社交媒体 | "若 harness 靠增加人工编写结构来扩展，可能在对抗 Bitter Lesson" |
| 14 | [mlpills #109: Intent Classification for AI Agents](https://mlpills.substack.com/p/issue-109-intent-classification-for) | Newsletter | 意图分类定义 |

**来源质量说明**：核心权重在 #1（官方）、#2（最贴题且有量化）、#3（提供了最关键的理论区分）、#4/#5（同行评审前的 arXiv，结论可参考但未经同行评审）。#13 为社交媒体观点，仅作趋势信号。检索中**未找到**直接研究"弱模型分类门对强模型执行质量影响"的实证文献——这是本次调研的主要空白。

## Rerun Inputs

```
workflow: firecrawl-deep-research
topic: 前置意图门（classify_node）要不要恢复 —— 含"前置门对模型能力发挥的影响"与"弱模型给强模型做前置决策是否能力倒挂"
depth: thorough
output: markdown
queries_used: 10 (见 Sources 对应的 8 个角度 + 2 个补充)
sources_scraped: 5 (tianpan / dev.to-wonderlab / miessler / arxiv-2609.07786 / mbrenndoerfer)
credits_used: ~25
notes: 未覆盖的空白——(a) 弱模型分类 + 强模型执行的直接实证；(b) 中文企业知识问答场景的流量分布数据
```
