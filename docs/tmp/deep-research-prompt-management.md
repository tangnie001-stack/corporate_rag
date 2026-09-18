# Prompt 管理调研

生成日期：2026-09-17 ｜ 一手来源：本仓库源码 + 容器实测 + 外部官方文档（见各节）

> 背景：由 `trace_c54ce259` 的"KB 检索 4 次 + 空答案"排查引出。
> 排查结论指向一个**配置层静默覆盖**：智能体预设整体替换了内置系统提示词，导致内置的"检索阶梯"丢失。
> 本文把问题从"一个 bug"提升到"prompt 该怎么管"。
>
> 结构：第一部分本项目现状穷举（源码 + 容器实测）｜第二部分外部最佳实践（官方一手）｜第三部分本地 10 个参考项目实读｜第四部分综合方案。
> 第四部分是**综合判断**，与前三部分（事实）分区呈现；外部无权威答案的问题在 2.7 显式列出，不假装有。

---

# 第一部分：本项目 prompt 现状穷举

## 1.1 七个来源（含子类）

### A. 静态常量层 —— `src/config/prompts.py`（322 行，19 个常量）

按用途分五组：

| 组 | 常量 | 消费方 |
|---|---|---|
| 人设/基础段 | `FINANCIAL_SYSTEM_PROMPT`（含规则 1-12） | `prompt.py` / `prompt_manager.py` |
| 环境约束层 | `KB_BOUND_RETRIEVAL_DISCIPLINE`、`KB_UNBOUND_SYSTEM_PROMPT`、`INLINE_CITATION_INSTRUCTION`、`DELEGATE_GUIDANCE_SECTION` | `prompt.py`（**条件追加**） |
| 用户消息模板 | `USER_PROMPT_TEMPLATE` | `prompt_manager.py` |
| 离线 LLM 任务 | `CLASSIFIER_SYSTEM_PROMPT` + `CLASSIFIER_USER_TEMPLATE`、`REWRITE_SYSTEM_PROMPT` + `REWRITE_USER_TEMPLATE`、`ENTITY_EXTRACTION_SYSTEM_PROMPT` + `ENTITY_EXTRACTION_USER_TEMPLATE` | `query_router.py`、`document_entity_extractor.py` |
| 运行时注入 | `VERIFY_GUIDANCE_PROMPT`、`VERIFY_HINT_PROMPT`、`VERIFY_CITATION_GUIDANCE_PROMPT`、`VERIFY_KB_CITATION_GUIDANCE_PROMPT` | `verify/regen_decision.py`、`verify/guardrails.py` |
| 子代理（fork） | `FORK_DEFAULT_EXECUTOR_PROMPT`、`FORK_EXECUTION_CONTRACT`、`FORK_TASK_APPEND_TMPL` | `agents/skills/executor.py` |

### B. 远端可覆盖层 —— `PromptManager`（Langfuse）

`src/infra/llm/prompt_manager.py`

- **只覆盖 3 个名字**：`financial-system-prompt` / `user-prompt-template` / `classifier-prompt`
- HTTP 拉取 `/api/public/v2/prompts/{name}`，60 秒缓存，失败落本地 fallback（`Event.PROMPT_FALLBACK`）
- **只取 latest，不做版本/label 固定**：`data.get("version")` 只写进日志（`:126`），不参与取用
- → 结论：**19 个常量里只有 3 个有远端版本能力；其余 16 个只能改代码发版**

### C. Markdown 内容库层

| 来源 | 路径 | 进入 prompt 的方式 | 语义 |
|---|---|---|---|
| 智能体预设 | `agents/<name>.md` | `build_system_prompt` 中 `base = persona` | **整体替换**基础段（`prompt.py:48-53`） |
| 技能正文 | `skills/<name>/SKILL.md` | 作为带 `[[skill-injection]]` 前缀的 **user 消息**落 Redis+MySQL（`agent_service.py:1118`），再由 `_initial_messages` 抽成独立 HumanMessage 放在 system 段之后（`agent_node.py:89-121`） | **叠加**，且**跨轮持久化** |

### D. 运行时代码拼接

| 位置 | 行为 |
|---|---|
| `rag/prompt.py:26-77` `build_system_prompt` | 人设 + 4 段条件追加（引用指令 / 委派引导 / 检索纪律 / KB 未绑定）+ 日期 |
| `agent_node.py:62-130` `_initial_messages` | system 段 + 注入消息 + `_truncate_history` 截断后的历史 + 当前 query |
| `verify/regen_decision.py:140,148` | 运行时 `SystemMessage` 注入联网指引 / hint |
| `verify/guardrails.py:92,171` | 运行时 `SystemMessage` 注入引用标注指引 |
| `agents/skills/executor.py:279-295` | `preset.system_prompt + FORK_EXECUTION_CONTRACT` |

### E. 工具描述本身（也是 prompt 面）

`retrieve_kb` / `ask_user` / `search_web` / `delegate_task` 的 docstring 经 `bind_tools` 变成模型的工具说明。

- **工具注册是条件式的**（`rag_tools.py:241-248`）：`search_web` 受 `settings.WEB_SEARCH_ENABLED` 控制（默认 true），`delegate_task` 受是否为 None 控制
- **但其他 prompt 里对它们的引用是无条件的** → 见 1.3

### F. 重复与分叉（新发现）

| 位置 | 问题 |
|---|---|
| `src/cli/compare_rewrite.py:42,73,100,119` | 复制了一份 classifier / rewrite prompt（`BUNDLED_CLASSIFY_*` / `INDEPENDENT_REWRITE_*`），与 `src/config/prompts.py` 的同名逻辑**两处维护** |
| `src/cli/eval_ragas_generate.py:118` | 代码内联 prompt（纠错清洗），未进 `prompts.py` |

### G. 测试层给出的**假保证**（新发现，最关键）

| 测试 | 断言 | 问题 |
|---|---|---|
| `tests/config/test_prompt_web_search.py:6-20` | 9 个短语（含 `"search_web"`、`"换一种问法"`、`"top_k=10"`）在 **`FINANCIAL_SYSTEM_PROMPT` 常量**里 | 只证明**常量完整**，不证明它到达模型 |
| `tests/rag/test_prompt_layers.py:47-60` | `assert "基础段正文" not in content` | **反向锁死缺陷**：把人设替换基础段当成契约固定下来 |
| `tests/rag/test_prompt_layers.py:72-78` | persona 非空时注入 `KB_BOUND_RETRIEVAL_DISCIPLINE` | 只保证"有检索纪律"，不保证**内容完整** |

**三者合起来的效果**：6 个 prompt 测试文件全部通过，而**没有任何一条测试断言"组装后送给模型的文本包含检索阶梯"**。缺陷从测试网里穿过去了。

## 1.2 组装时序（一次请求里 prompt 被拼了几次）

```
请求入口
  │  session 绑定智能体 → persona 正文写入 RequestContext
  │  skill 命中 → 正文写成 [[skill-injection]] user 消息（落库，跨轮）
  ▼
build_prompt(query, context="", history, pm, kb_bound, persona, has_skills)
  ├─ build_system_prompt(persona, kb_bound, has_skills, pm)
  │    base = persona 或 pm.get_base_system_prompt()      ← 来源 A/B 二选一
  │    + KB_BOUND_RETRIEVAL_DISCIPLINE   if kb_bound and persona
  │    + INLINE_CITATION_INSTRUCTION     if not in base
  │    + DELEGATE_GUIDANCE_SECTION       if has_skills or not persona
  │    + _with_current_date(base)
  │    + KB_UNBOUND_SYSTEM_PROMPT        if not kb_bound
  ├─ 历史消息（_truncate_history：最近 N 轮 + token 预算）
  └─ pm.get_user_template(context="", query)               ← USER_PROMPT_TEMPLATE
  ▼
agent_model（每轮复用 state.messages，不重新组装）
  ├─ 轮 1..N：LLM ↔ tools（工具描述来自来源 E）
  ▼
agent_finalize → verify
  └─ 运行时注入 VERIFY_* SystemMessage → regen 回 agent（_agent_iterations 复位）
  ▼
fork 子代理（若 delegate）：executor.py 另起一条
  └─ preset.system_prompt + FORK_EXECUTION_CONTRACT     ← 与主路径不同的拼装逻辑
```

## 1.3 三个已实证的问题

### 问题 1：人设整体替换，静默丢失系统级协议

`prompt.py:48-53` 的 `if persona: base = persona` 是**整体替换**。

被替换掉的 `FINANCIAL_SYSTEM_PROMPT` 里含检索阶梯：

```
4. 检索结果为空或全部明显不相关时：换一种问法重新调用 retrieve_kb（第二次检索显式传 top_k=10）
5. 再次检索仍无相关结果时：说明"该问题不在当前知识库范围内"，再调用 search_web
6. 若 search_web 也无法获取 → 最后才说明"未在文档中找到相关数据"
7. 检索结果相关但不足以回答时：按已有内容作答并说明证据不足，或调用 ask_user 澄清，不得编造
8. 知识库能回答的问题不要调用 search_web
```

实测后果（`trace_c54ce259`）：模型收到的唯一检索指令是
- system：「必须先调用 retrieve_kb 检索」（无停止条件）
- user：「若参考文档不足以回答，请使用工具获取所需信息；**若工具仍无法获得相关信息**，再说明…」

而 `retrieve_kb` 每次成功返回 2 条、从不返回空 → **"工具仍无法获得"这一格永远不会亮** → 4 次检索 + `answer_len=0`。

`prompt.py:54-57` 的注释记录了当初不注入的理由：

```
# persona 为空时人设层即 FINANCIAL_SYSTEM_PROMPT，其处理流程 2–9 已含"先检索后作答"，
# 无条件注入会破坏"默认行为逐字不变（端到端快照）"需求。
```

即：**假设"persona 非空时由预设自己承载等价指引"，但 `agents/finance-expert.md` 里根本没有。** 保护措施是"不注入以防破坏快照"，代价是假设落空后无兜底。

### 问题 2：工具条件注册 vs 无条件提示词引用（契约泄漏）

| prompt 位置 | 引用 | 工具是否恒可用 |
|---|---|---|
| `FINANCIAL_SYSTEM_PROMPT` 规则 5/6/8（`prompts.py:52,53,55`） | `search_web` | ❌ 条件注册 |
| `VERIFY_GUIDANCE_PROMPT`（`prompts.py:267`） | "请调用 search_web 工具补充…" | ❌ 且是**运行时注入** |
| `VERIFY_HINT_PROMPT`（`prompts.py:277`） | "请**再**调用一次 search_web" | ❌ 且要求"再调" |

→ 关闭 `WEB_SEARCH_ENABLED` 后，模型被明确命令调用一个未绑定的工具（`bind_tools` 只绑定已注册工具）。

### 问题 3：同一职责的 prompt 在两条路径上语义不一致

| | 主 agent 路径 | fork 子代理路径 |
|---|---|---|
| 人设与系统约束的关系 | **整体替换**（`prompt.py:48-53`） | **追加**（`executor.py:291-295`） |
| 不可覆盖的执行约束 | 无（只有内容不完整的 `KB_BOUND_RETRIEVAL_DISCIPLINE`，且注入条件依赖 persona 非空） | **`FORK_EXECUTION_CONTRACT` 无条件追加** |
| 代码里的设计声明 | 无 | `prompts.py:317`："契约是执行约束，**不随执行者人设（preset）内容作者意愿而增减**" |

**结论：项目内部已经存在正确模式，只是没用在主路径上。**

## 1.4 来源汇总表

| # | 来源 | 存放位置 | 谁能改 | 有无版本 | 覆盖/叠加 |
|---|---|---|---|---|---|
| A | 静态常量 | `src/config/prompts.py` | 改代码发版 | 无 | — |
| B | 远端 prompt | Langfuse（3 个名字） | 运营后台 | **有版本字段但不用** | 覆盖 A 的 3 个 |
| C1 | 智能体预设 | `agents/*.md` | 内容作者 | 无 | **整体替换** A |
| C2 | 技能正文 | `skills/*/SKILL.md` | 内容作者 | 无 | **叠加**（跨轮持久） |
| D | 运行时拼装 | `prompt.py` / `verify/*` / `executor.py` | 改代码发版 | 无 | 叠加 |
| E | 工具描述 | 工具 docstring | 改代码发版 | 无 | 叠加（进 tools schema） |
| F | 复制分叉 | `cli/compare_rewrite.py` 等 | 改代码发版 | 无 | 各自独立 |
| G | 测试 | `tests/**/test_prompt*` | 改代码发版 | — | **测错层** |

**七个来源里，有四个能独立改变模型行为，其中 C1 的语义是"替换"而非"叠加"，且没有任何机制保证替换后系统级约束仍存在。**

---

# 第二部分：外部最佳实践

来源编号见文末「来源清单」。Anthropic 官方文档站在本机被区域重定向，结论一律取自其 engineering 博客 / GitHub 官方源码 / 官方教程，均为一手。

## 2.1 Q1：工具可用性与提示词段落怎么绑定

**结论：没有权威术语（"capability-gated prompt sections" 未找到任何厂商命名）。但官方共识明确——工具契约属 tool definition 层，prompt 层只负责"有哪些工具可用"。**

| 依据 | 内容 |
|---|---|
| OpenAI cookbook（S9） | "We encourage developers to **exclusively use the tools field** to pass tools, rather than manually injecting tool descriptions into your prompt"；量化：用 API 解析的工具描述比手抄进 system prompt，SWE-bench Verified **+2%** |
| Anthropic（S1） | 工具定义由 API **自动注入** system prompt；"how tools are **dynamically loaded** into Claude's system prompt" |
| Anthropic（S2） | `defer_loading: true` 的工具"**aren't loaded into Claude's context initially**"——未加载的工具模型**根本看不见** |
| OpenAI cookbook（S9） | **与我们问题 2 同款**："if told 'you must call a tool before responding to the user,' models may **hallucinate tool inputs or call the tool with null values**" |
| Anthropic（S3） | 推荐 section 化组织，"`## Tool guidance`"是官方认可的独立段落，天然适合做条件注入的边界 |
| LangChain（S24） | `@dynamic_prompt`（改指令）与 dynamic tool selection（改工具集）是**两套独立 middleware，官方未要求同步** |

→ **最后一条是关键**：连 LangChain 官方文档都没有要求"动态工具选择"与"动态 prompt"保持同步。这正是漂移 bug 的同类根因——不是我们独有的疏忽，是**框架层的空白**。

**可复用的三种模式（有官方原型，但无统一命名）**：
1. **工具用法写进 tool description**（Anthropic 首选："think of how you would describe your tool to a new hire"）
2. **progressive disclosure / defer_loading**（Anthropic Skills + Tool Search）——能力出现时提示词才出现
3. **routine = 指令 + 工具的组合单元**（OpenAI cookbook S10：`routine` 定义就是 "a list of instructions… **along with the tools necessary to complete them**"，handoff 时两者**一起切换**）

## 2.2 Q2：persona 覆盖与分层组合

**结论：没有任何官方机制支持"persona 整体替换 system prompt"。三家官方机制的默认全是叠加。**

| 官方机制 | 语义 | 出处 |
|---|---|---|
| Claude Agent SDK `SystemPromptPreset{type:"preset", preset:"claude_code", append:str}` | **追加** | S7（官方源码） |
| Claude Agent SDK `SystemPromptCustom{type:"custom", prompt:str}` | **替换**（显式） | S7 |
| OpenAI Agents SDK `RECOMMENDED_PROMPT_PREFIX` | 源码即 `f"{prefix}\n\n{prompt}"`——**前缀组合**，子 agent 覆盖不掉 | S13（官方源码） |
| Anthropic 复杂 prompt 十元素结构 | 人设（TASK_CONTEXT）是 **10 分之 1**，其后还有 rules / examples / format | S6（官方教程） |

Anthropic 给的结构顺序（S6）：`user role → Task context(人设) → Tone → rules → Examples → Input data → Immediate task → Precognition → Output formatting → Prefill`，并给出 `if TASK_CONTEXT: PROMPT += ...` 式条件拼接代码。

→ **结构上不存在"人设替换全部"的空间。** 我们的做法属于 Claude Agent SDK 的 `custom` 语义——而官方用 `custom` 时**确实不替你保留 base**，所以真要"换人设"，必须自己做装配。

**另外两条**：
- Claude Agent SDK 的 `exclude_dynamic_sections`（S7）：把每用户动态段（工作目录/记忆/git 状态）从 system 剥离、改注入首条 user 消息，保证静态前缀可跨用户命中缓存 → **这就是我们"环境约束层分段注入"诉求的官方实现**
- Anthropic「prompt altitude」（S3）："specific enough to guide behavior effectively, yet flexible enough to provide the model with strong heuristics"

## 2.3 Q3：prompt 版本化与集中管理

**分歧真实存在，双方论点都要认。**

| | 远端托管（Langfuse / LangSmith / Bedrock） | 仓库托管（12-Factor Agents / Agent Skills） |
|---|---|---|
| 主张 | 非工程人员可改、免发版即时生效、label 灰度、集中审计 | prompt 是一等代码：可 review / diff / 回滚 / CI / 本地复现 |
| 出处 | S17 / S19 / S22 | S29（"own your prompts and treat them as first-class code"）、S5/S8 |
| 代价 | 可用性依赖 + 客户端缓存导致"改了不生效"；**线上版本与仓库不一致，不可复现** | 改 prompt 要发版 |

**必须注意的三点：**

1. **Langfuse 自己承认的坑**（S17/S18）：
   - 官方称 Prompt Management "is **not on the critical path**"，SDK **客户端缓存**，首次拉取后从内存读
   - **冷启动空缓存会抛异常**；官方给两个方案：① 启动预取、失败即退出进程；② 传 `fallback=` 并用 `prompt.is_fallback` 判断
   - 官方 FAQ 里"改了不生效"（"I'm not seeing the latest version of my prompt. Why?"）是常见困惑
2. **LangSmith 的做法值得单独记**（S19）：`pull_prompt("joke-generator:production")`——**引用 tag 而非 commit id，从而"不修改代码就能切换版本"**
3. **LangSmith 的论据是产品论据不是技术必然**（S20）：理由原文是"the most effective prompt engineer may be a product manager, domain expert, or other non-technical team member"→ **采用前需自评是否有这个协作需求**

**最关键的一条判断（对我们直接相关）**：
> 远端方案**没有解决"代码里那份副本会漂移"的问题**。若采用"远端 + 本地 fallback"，**本地 fallback 必须被视为版本化的一等资产**（有 owner、有评审、有版本号），否则它就是隐藏的第二事实源。

→ **这正是我们"5 处存放"的成因。**

## 2.4 Q4：防漂移的工程手段

**结论："prompt contract test" 不是权威术语**，只找到社区项目（`prompt-contracts` PyPI、`prompt-ci` 等），**不建议作为规范依据**。同样，**prompt lint 无厂商级方案**，社区工具成熟度低，**容易产生虚假安全感**。

**权威且可用的三层：**

| 层 | 手段 | 出处 |
|---|---|---|
| 1 | **输出断言 / 回归评估**（成熟、厂商官方）：promptfoo 断言 "compare the LLM output against expected values or conditions"，面向 CI | S30 |
| 2 | **请求载荷断言（最接近 contract test，官方已提供原语）** | S15 |
| 3 | eval 驱动 A/B，再固化 | S1 / S3 / S4 |

第 2 层是重点。OpenAI Agents SDK 的 `ScriptedModel` 记录每次模型调用，"Common assertions include `call.input`, `call.model_settings`, **`call.tools`**, `call.handoffs`"；`assert_complete()` 用于 "Detect workflow drift"（多一次调用抛 `UnexpectedModelCall`，少一次抛 `UnconsumedModelSteps`）。

→ **这意味着可以直接断言：本轮 prompt 文本里出现的工具名 ⊆ `call.tools` 的名字集合。这是当前唯一有官方原语支撑的"prompt ↔ 工具契约"断言。** 而纯输出层断言**发现不了**"prompt 引用了未注册工具"这类结构漂移。

## 2.5 Q5：多 agent 的 prompt 传递

**Anthropic 给的子 agent 必需要素清单（S4）**，可直接照搬成我们的委派模板：

> Each subagent needs an **objective, an output format, guidance on the tools and sources to use, and clear task boundaries**. Without detailed task descriptions, agents duplicate work, leave gaps, or fail to find necessary information.

**LangChain 把子 agent 上下文注入模式正式命名了（S26）**：

| 模式 | 语义 | tradeoff |
|---|---|---|
| `mode: "isolated"`（**默认**） | 只传任务描述，clean context window | "re-derives everything the parent already did" |
| `mode: "fork"` | 传父的**完整消息历史（含 tool calls）与 system prompt** | "Seeded with prior context, **at the cost of a larger prompt and less isolation**" |

**三条可直接用的提醒**：
- LangChain（S26）：子 agent 常见失败是"performs tool calls or reasoning but **doesn't include results in its final message**"→ 要提醒它"supervisor 只看最终输出"
- OpenAI（S12）：handoff 时用 `input_filter` 裁剪历史（提供 `handoff_filters.remove_all_tools` 等）
- Anthropic（S4）：要"teach the orchestrator how to delegate"，并给 effort 分级（简单事实 1 agent / 3–10 calls；复杂研究 >10 subagents）

## 2.6 有坑的流行做法（与本项目对照）

| 坑 | 出处 | 与我们的关系 |
|---|---|---|
| 把 tool schema 手抄进 system prompt | S9（官方量化 -2%） | 我们没犯 |
| **无条件强令调用工具** | S9："may hallucinate tool inputs or call the tool with null values" | **正是问题 2 的官方描述** |
| **让 persona 走"整体替换"** | S7（`custom` 确实替换，官方不兜底） | **正是问题 1**；"检索纪律丢失是必然，不是模型变笨" |
| "远端 prompt 就没问题了" | S17/S18 | 我们已有 3/19 在 Langfuse，且**不做版本固定** |
| prompt lint 当门禁 | 无厂商方案 | 我们没做，**不要做** |
| subagent 一律 fork | S26（prompt 更大、隔离更差） | 我们的委派是 fork，需评估 |
| 只做输出层评估 | S15 | 我们的 6 个 prompt 测试全部只测常量/输出，**未测请求载荷** |

## 2.7 未找到权威答案的问题（不要假装有）

1. "capability-gated prompt sections" 的官方术语或规范 —— **未找到**
2. "功能开关关闭时，其 prompt 段落应如何处置"的官方明文 —— **未找到**（属推断性最佳实践）
3. "prompt must not reference unregistered tools" 的显式规范 —— **未找到**；只能由 S9 的幻觉警告 + S2 的 defer_loading 语义组合推出
4. "prompt contract test" 的权威定义 —— **未找到**（只有社区项目）
5. prompt 静态 lint 的厂商级方案 —— **未找到**
6. Vertex AI Prompt Registry / OpenAI versioned prompts 一手原文 —— 站点受限未核验（S32/S33）

---

# 第三部分：本地参考项目（10 个仓库实读）

## 3.1 三个梯队

**第一梯队：直接可抄（与本项目问题域重合）**

| 仓库 | 一句话 |
|---|---|
| **deepseek-harness** | 单一 registry + 命名 order 表 + 变量注册表；**persona 默认只 shadow 一个段，整体替换需显式 `complete:true`**；工具与其 prompt section 在同一处注册 |
| **WeKnora** | 同领域。**九段模型**（base/steering/runtime_contract/sources/tools/skills/output/memory/protocol）；**自定义正文只换 `base`，运行时各段强制保留**；条件注入按**实际注册的工具集合**判断 |
| **claude-code** | 静态段 + 动态段注册表 + 五级优先级；`enabledTools` 集合逐条判 null；**64 条 prompt 契约断言测最终拼装结果**；fork 继承父**已渲染字节** |

**第二梯队：单点可借**

| 仓库 | 借什么 |
|---|---|
| **codex** | `PromptSlot` 命名槽位；**指令变更显式通知**（`REPLACEMENT_NOTICE` / `REMOVAL_NOTICE`）；insta prompt 版面快照 |
| **financial_rag** | 同领域。prompt 目录化（`agents/<name>/{agent.yaml,system.md}` + `shared/*.yaml`，yaml 声明 includes）；渲染器**只替换标识符式占位符、未知变量原样保留** |
| **ragflow** | `citation_prompt.md`（引用指令 + 6 个正反例）；`DiscoverInstructionFiles` 向上找 AGENTS.md/rules + 字符预算截断 |
| **fastapi-langgraph 模板** | 最小范式：编译期读 md 到常量，每请求只填变量（"no file I/O per request"） |
| **dify** | 按「模型 × 应用模式」组合模板；mention token `[§kind:id§]` 指代资源 |

**第三梯队：无亮点**（确认即可）
`langgraph`（prompt 完全交应用，唯一可借是 `prompt` 可为 `callable(state)->messages`）、`Qwen-Agent`（常量与 agent 同文件）、`awesome-llm-apps`、`fastapi-0.141.1`、`full-stack-fastapi-template`、`fastapi-best-architecture`。

## 3.2 横向对比

| 仓库 | 分层 | 条件注入 | persona 覆盖语义 | prompt 契约测试 |
|---|---|---|---|---|
| deepseek-harness | **命名 order 表** + 平局按 name | **工具包 apply 时与工具同处注册**（最干净） | **默认只 shadow**；替换要 `complete:true`（默认 false，且只允许一个） | 有，624 行 spec（验 order 唯一 / 间隔≥10 / 注册序无关） |
| WeKnora | 九段模型，唯一入口，空段丢弃 | **按实际 registry 求交**，注释明说"不用配置开关" | **自定义正文换 `base`，运行时各段保留** | 有，boundary 测试钉"自定义正文下边界不丢" |
| claude-code | 静态/动态分区 + 五级优先级 | `enabledTools` 逐条判 null，空段 filter 掉 | persona 只是**一个 section**；替换需显式；proactive 下改 append | **有，64 条 `toContain` 断言测最终输出** |
| codex | 替换链 | 命名槽位 `PromptSlot` | personality 选 md，base 替换 | 有（insta 版面快照） |
| financial_rag | yaml 声明 includes | 有循环/条件语法，**不按工具可用性** | 无明确说法 | loader/engine 单测 |
| dify | 无（模板整体替换） | 弱（`#context#` 空则填空） | `pre_prompt` 即全部 | 单测，无契约 |
| ragflow | `PromptBuilder` **定义了但未接线** | 无 | 无 persona 概念 | 无 |
| langgraph / Qwen-Agent / awesome-llm-apps / 三个 Web 模板 | 无 | 无 | 无 | 无 |

## 3.3 值得抄的做法（按价值排序，只列前 8）

1. **人设降级为一个可覆盖的段，叠加是默认、替换是显式 opt-in** — WeKnora `internal/agent/prompts.go:400-470`；deepseek-harness `packages/preset/persona/src/index.ts:37-60`。→ 直接修问题 1。
2. **按实际注册的工具集合条件注入指引段** — WeKnora `internal/agent/grounding_prompt.go:17-80`（`if slices.Contains(names, tools.ToolWebSearch)`，文件头注释："uses the current registry, not configuration flags that may name filtered-out tools"）；deepseek-harness `packages/fs/tool-fs/src/read.ts:68-73`（工具与 section 同处 `applyXxxTool()`，随插件生命周期消亡）。→ 直接修问题 2。
3. **建立「规则归属表」，禁止同一规则写在多处** — WeKnora `docs/agent-prompt-assembly.md` 末节（角色与方法→`base`；查什么→`sources`；怎么调用→工具定义/`tools`；输出形态→`output`；引用编码→`protocol`；并明写"不要把同一条规则复制到多个模板中来'加强优先级'"）；deepseek-harness Agent Note `2026-07-05-prompt-variables-and-tool-guidance-ownership.md`（**"every fact in the prompt has exactly one owner"**）。→ 治问题 3 的根。
4. **prompt 契约测试写成对最终拼装结果的断言** — claude-code `src/constants/promptEngineeringAudit.runner.ts`（64 条，子进程隔离）；WeKnora `internal/agent/prompt_composition_test.go`、`grounding_prompt_test.go::TestGroundingUsesRegistryInsteadOfConfiguration`、`chat_pipeline/prompt_boundary_test.go::TestSourceBoundarySurvivesNormalAndIntentCustomPrompts`。→ **唯一可靠的防回归手段**，且与我们 pytest 体系天然兼容。
5. **每段按 section 打字节数日志** — WeKnora `docs/agent-prompt-assembly.md`「维护入口」：`[Agent][Prompt] section=... bytes=...`，空段不输出。改动一行，排查收益极大。
6. **fork 子 agent 继承父「已渲染的 prompt 字节」** — claude-code `AgentTool/forkSubagent.ts:56-70`（注释：重新调 `getSystemPrompt()` 会因配置冷/热漂移而 bust cache）。→ 我们 bug 5 若重新拼装，父子前缀不一致会同时造成缓存失效与纪律不一致。
7. **子 agent 显式声明「父 prompt 里哪条不适用于我」** — claude-code `AgentTool/forkSubagent.ts:161-190`：硬规则第 1 条 `"Your system prompt says 'default to forking.' IGNORE IT — that's for the parent. You ARE the fork. Do NOT spawn sub-agents."`。→ 我们委派 fork 会继承主 agent 的"委派引导"，很可能导致子 agent 再委派。**解法是在子 agent 首条消息里作废，而不是从 system 里删**（删了破坏前缀一致性）。
8. **子 agent 的委派文案按「是否继承上下文」生成两套** — deepseek-harness `packages/subagent/tool-subagent/src/index.ts:250 providerWording(inheritsConversation)`（注释：给 fork 写"它看不到本对话"是假话）。

其余可抄（略）：模板继承只存"偏离默认值的覆盖项"（WeKnora `frontend/src/utils/agentPromptTemplates.ts:15-24`）、指令变更显式通知旧指令作废（codex `persistent_mode.rs` 的 `REPLACEMENT_NOTICE`/`REMOVAL_NOTICE`）、引用指令独立成文件并补正反例（ragflow `citation_prompt.md`）、向上查找 AGENTS.md + 字符预算（ragflow `prompt_builder.go:87+`）、不引模板引擎只做标识符式占位符（financial_rag `loader.py:312-341`）。

## 3.4 有坑（明确不要抄）

| 坑 | 出处 | 为什么 |
|---|---|---|
| **从渲染好的文本里剥标题删段** | codex `core/src/context/update_plan_instructions.rs:4` | 按 markdown 标题匹配删除，标题一改就静默失配。必须用条件拼装 |
| **提示词硬编码命令调用某工具、无可用性判断** | financial_rag `app/prompts/shared/tool_fallback.yaml`（"知识库无内容时**必须**调用 `search_web`"） | **正是我们问题 2 的同款**。拿它当反面案例 |
| **设计完不接线** | ragflow `internal/harness/core/prompt_builder.go`（`PromptSection` 除定义处外零调用点，`react_agent.go` 实际走 `DefaultSystemPrompt`） | 读到它别以为 ragflow 有分层；也提醒"设计完不接线等于没有" |
| **两套模板语法并存** | dify（工作流 Jinja2 + 应用 `{{#var#}}`） | 纯认知负担。只保留一套标识符式占位符 |
| **DB prompt 版本表 + A/B + optimizer** | financial_rag `app/models/prompt_version.py`（status/ab_test_weight/成功率/评分） | 功能全但 prompt 变运行时数据：**编译期无检查、code review 看不到、测试难覆盖**。现阶段不该上 |
| **prompt 常量与 agent 同文件** | Qwen-Agent `agents/router.py:24` | 我们现状就是这样，不要强化 |

## 3.5 五个问题 → 参考映射

| 本项目问题 | 最直接可抄 |
|---|---|
| 1 人设整体替换 | WeKnora `prompts.go:400`（自定义只换 `base`）+ deepseek-harness `persona/src/index.ts:37`（默认 shadow，`complete:true` 才替换） |
| 2 工具注册 vs 提示词引用 | WeKnora `grounding_prompt.go:17`（按 registry 判断）+ deepseek-harness `tool-fs/src/read.ts:68`（工具与 section 同处注册） |
| 3 system 段多源拼接 | deepseek-harness 单一 registry + 命名 order（`system-prompt/src/index.ts:121`）+ 归属规则 Agent Note + WeKnora 九段模型 |
| 4 verify 运行时塞 SystemMessage | codex `REPLACEMENT_NOTICE`/`REMOVAL_NOTICE` + claude-code `enhanceSystemPromptWithEnvDetails`（`prompts.ts:728`，统一追加入口） |
| 5 主→子 agent prompt | claude-code fork 继承已渲染字节（`forkSubagent.ts:56`）+ `buildChildMessage` 作废父编排指令（`:161`）+ deepseek-harness `providerWording`（`tool-subagent/src/index.ts:250`） |

**若只做三件事**：① 建规则归属表（文档，先于代码）；② 人设从"替换"改为"叠加 + 显式接管开关"；③ 加一组 prompt 契约测试（对**最终拼装结果**断言"检索阶梯仍在""未注册工具不被提及"）。

# 第四部分：综合方案

> 说明：本部分是**综合判断**，不是新调研。凡引用前文处标注来源节号；
> 凡属我的推断，标「推断」。外部没有权威答案的问题（见 2.7）不假装有。

## 4.1 三条原则（先文档，后代码）

### 原则 1：一条事实只有一个 owner

出处：deepseek-harness Agent Note `2026-07-05-prompt-variables-and-tool-guidance-ownership.md`（**"every fact in the prompt has exactly one owner"**，见 3.3-3）+ WeKnora `docs/agent-prompt-assembly.md` 末节规则分工表（3.3-3）。

**为什么这是根**：我们有七类来源（1.1），乱的原因不是"来源多"，而是**没有任何文档规定"哪一类规则住哪一层"**。所以每次加规则都在找"哪儿能塞进去"，塞完就重复、就漂移。**没有归属表，后面每个改动都在打架。**

归属表草案（迁移自参考项目 + 适配本项目）：

| 事实类型 | 应归属 | 本项目现状 |
|---|---|---|
| 模型名 / 工作目录 / 日期 | 变量 | 日期已由 `_with_current_date` 处理；无模型名变量 |
| 单工具语义、"何时用我" | **工具 `description`** | 已有（`retrieve_kb` docstring 有"何时调用"） |
| **跨调用的工具使用习惯** | **工具能力段（capability section）** | **缺** ← 检索阶梯本应在这里，现在却住在 `FINANCIAL_SYSTEM_PROMPT`（人设层） |
| 身份 / 领域角色 | **人设 overlay** | `agents/*.md`，但语义是**替换** |
| 输出形态 / 引用编码 | 独立段 | `INLINE_CITATION_INSTRUCTION`，已在环境约束层 ✅ |
| 子代理执行契约 | 子代理段 | `FORK_EXECUTION_CONTRACT`，**已正确** ✅ |

→ **最本质的一步是重分类：检索阶梯从"人设层"搬到"工具能力段"。**
它描述的是"跨调用的工具使用习惯"，不是"我是谁"——放错层才导致被 persona 覆盖。这同时解释了为什么子代理那条路没出问题：`FORK_EXECUTION_CONTRACT` 一开始就放对了层（3.5 映射）。

### 原则 2：能力是注册单元，工具与其提示词同源

出处：OpenAI cookbook 的 `routine`（**"a list of instructions… along with the tools necessary to complete them"**，2.1）+ WeKnora `grounding_prompt.go:17-80` 按实际 registry 判断（3.3-2）+ deepseek-harness `tool-fs/src/read.ts:68-73` 在同一个 `applyXxxTool()` 里注册工具与 section（3.3-2）。

```
capability = { name, tools[], prompt_sections[] }
  ├─ 注册 → tools 进 ToolRegistry，sections 进 prompt 组装
  └─ 未注册 → 两者一起消失
```

**外部没有这个模式的权威命名**（2.7-1），但三家都有原型。**推断**：我们不必立刻做成完整注册表，最简形态即可满足原则 2 —— 让 prompt 组装接收 `enabled_tools` 集合，逐段判 null（WeKnora 路线，改动最小）。

### 原则 3：叠加是默认，替换是显式且危险

出处：Claude Agent SDK 的 `SystemPromptPreset{type:"preset", append}` vs `SystemPromptCustom{type:"custom"}`（2.2）+ deepseek-harness persona 默认只 shadow、`complete:true` 才整体替换（3.3-1）+ WeKnora 九段模型（3.3-1）。

```
system = 静态 BASE（含默认人设）
       + persona_overlay          ← 只覆盖"人设段"，不动约束段
       + capability_sections      ← 按注册的工具集合渲染（原则 2）
       + 动态尾部（日期等）
```

**外部明确结论**（2.2）：没有任何官方机制支持"persona 整体替换 system prompt"；我们现在的做法等于 SDK 的 `custom` 语义，而**官方用 `custom` 时不替你保留 base**。

## 4.2 分阶段落地

### P0 —— 纯文档 + 先写会失败的测试（不改行为）

1. **规则归属表落 `docs/agents/`**（归属待定，见 4.5-3）
2. **写 prompt 契约测试**——按外部第 2 层手段（请求载荷断言，2.4），**先写会失败的**：
   - 断言"选中预设后，最终 system 文本仍含检索阶梯要素"
   - 断言"未注册的工具名不出现在最终 prompt 里"
   - 断言"`build_system_prompt` 产出的工具名集合 ⊆ 实际注册工具名集合"

**P0 的价值**：不改一行行为，就把问题 1/2 从"没人知道"变成"测试红的"。这也符合项目自己的 TDD 约定。

### P1 —— 修问题 1 与问题 3（结构）

1. 把检索阶梯从 `FINANCIAL_SYSTEM_PROMPT` **拆出**，成为独立的环境约束段，**无条件追加**（照 `FORK_EXECUTION_CONTRACT` 的模式）
2. persona 语义从"替换 base"改为"覆盖人设段 + 追加约束段"
3. 更新 `tests/rag/test_prompt_layers.py:59`（它现在断言 `"基础段正文" not in content`——**这条断言保护的就是缺陷**）
4. **重采端到端快照基线**

### P2 —— 修问题 2（能力门控）

1. `build_system_prompt` 接收 `enabled_tools`，逐段判 null
2. 三处无条件引用改条件（1.3-问题 2）：`FINANCIAL_SYSTEM_PROMPT` 规则 5/6/8、`VERIFY_GUIDANCE_PROMPT`、`VERIFY_HINT_PROMPT`
3. **注意后两处是 verify 运行时注入**（`regen_decision.py`），需把工具集传到注入点

### P3 —— 可选，暂不建议

- prompt 外置成 md 文件（参考 fastapi-langgraph 模板的最小范式，3.3-9）
- 远端版本固定（Langfuse 已有版本字段，只需改 `_fetch_prompt` 用 label 而非 latest）
- 完整 section registry + 命名 order 表（deepseek-harness 路线）

## 4.3 明确不做（有依据的否决）

| 不做 | 依据 |
|---|---|
| prompt lint 当质量门禁 | **无厂商级方案**，社区工具成熟度低，容易产生虚假安全感（2.4 结论 + 2.6 坑 5） |
| DB prompt 版本表 + A/B + optimizer | financial_rag 有完整实现（3.3-其余），但代价是 prompt 变运行时数据：**编译期无检查、code review 看不到、测试难覆盖**（3.4）。我们现阶段不该上 |
| 引入 Jinja2 | dify 双语法并存的教训（3.4 坑 4）；需要变量替换时用 financial_rag `render_text` 那种**仅标识符式占位符**（3.3-其余） |
| 把 tool schema 手抄进 prompt | OpenAI 官方量化劣于 tools 字段（2.6 坑 1） |
| 从渲染好的文本里剥标题删段 | codex 反面做法，静默失配（3.4 坑 1） |
| 立刻做完整 section registry 重构 | P1+P2 已能覆盖三个问题；**过度设计**违反项目「最小改动」原则 |

## 4.4 与现有约束的冲突（必须先知道）

1. **两条测试会必然失败**：`tests/rag/test_prompt_layers.py:24`（persona='' 逐字不变）与 `:59`（基础段必须消失）。它们保护的正是要改的行为。**改测试不是"为了让测试过"，是契约本身变了**——需要在提交信息里说清。
2. **注释理由要重写**：`src/rag/prompt.py:54-57` 写的"无条件注入会破坏默认行为逐字不变（端到端快照）需求"，其前提假设（"persona 非空时由预设自己承载等价指引"）已被证伪（1.3-问题 1）。
3. **端到端快照基线要重采**——这是唯一需要人工确认的一步。
4. **远端/本地双事实源**：现在 3/19 个 prompt 在 Langfuse 且**不做版本固定**（1.1-B）。按外部结论（2.3），本地 fallback 必须被视为**版本化的一等资产**。这一条独立于 P0-P2，但会持续制造"线上与仓库不一致"。

## 4.5 需要你决策的点

1. **检索阶梯的版本**：完整保留规则 4-8（含"第二次检索 top_k=10"这类具体动作），还是精简为 3 条（何时重试 / 何时联网 / 何时如实说明）？
2. **persona 语义做到哪一步**：
   - (a) 只做"默认叠加"，不提供替换开关
   - (b) 叠加 + 显式 `complete` 开关（deepseek-harness 路线，且多于一个 complete 报错）
   - (c) 先 (a)，观察后再定 (b)
3. **归属表放哪**：a) `docs/agents/rules.md` 加一节；b) 新建 `docs/agents/prompt-ownership.md`（专项档，与 `logging-rules.md` 同级）；c) 并入已有 `docs/agents/glossary.md`
4. **本轮范围**：只做 P0（不改行为，暴露问题），还是 P0+P1（含结构调整）？

## 4.6 建议的下一步

这份文档是**调研**，不是决策，也不是实施方案。按项目自己的流程：

- **决策**（"检索阶梯归能力层不归人设层""能力门控是注册单元"）→ 写进 `docs/adr/`（不可逆的架构取舍）
- **执行** → 走 openspec change，把 P0/P1/P2 拆成 tasks
- **顺带**：这套结论会修订 `docs/superpowers/specs/2026-09-16-retrieval-exhaustion-early-stop-design.md`（那份提案的前提是"检索阶梯缺失 → 要新增止损信号"，而现在的结论是"**阶梯本来就有，只是被替换掉了**"——两个方向不同）

---

# 来源清单

## 一手（本仓库）

| # | 来源 | 用于 |
|---|---|---|
| 1 | `src/config/prompts.py`（322 行，19 常量） | 1.1-A |
| 2 | `src/infra/llm/prompt_manager.py`（Langfuse + fallback） | 1.1-B |
| 3 | `src/rag/prompt.py:26-77`（`build_system_prompt` / `build_prompt`） | 1.1-D、1.2、1.3 |
| 4 | `src/agents/graph/agent_node.py:62-130`（`_initial_messages`） | 1.2 |
| 5 | `src/agents/skills/executor.py:279-295`（`_executor_system_prompt`） | 1.3-问题 3、4.1 |
| 6 | `src/agents/tools/rag_tools.py:241-248`（工具条件注册） | 1.1-E |
| 7 | `tests/rag/test_prompt_layers.py:24,47-60,72-78` | 1.1-G |
| 8 | `tests/config/test_prompt_web_search.py:6-20` | 1.1-G |
| 9 | `agents/finance-expert.md` | 1.3-问题 1 |
| 10 | `docker exec` 实测日志 `trace_c54ce259`（`/data/logs/app_2026-09-16.log`） | 1.3-问题 1 |

## 外部（官方为主，编号与第二部分一致）

| 编号 | 来源 | 类型 |
|---|---|---|
| S1 | https://www.anthropic.com/engineering/writing-tools-for-agents | 官方博客 |
| S2 | https://www.anthropic.com/engineering/advanced-tool-use | 官方博客（defer_loading） |
| S3 | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | 官方博客（section 化 / altitude） |
| S4 | https://www.anthropic.com/engineering/multi-agent-research-system | 官方博客（子 agent 必需要素） |
| S5 | https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills | 官方博客（progressive disclosure） |
| S6 | https://github.com/anthropics/prompt-eng-interactive-tutorial/blob/master/Anthropic%201P/09_Complex_Prompts_from_Scratch.ipynb | 官方教程（十元素结构） |
| S7 | https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py | 官方源码（`SystemPromptPreset` / `SystemPromptCustom`） |
| S8 | https://github.com/anthropics/skills | 官方仓库 |
| S9 | https://github.com/openai/openai-cookbook/blob/main/examples/gpt4-1_prompting_guide.ipynb | 官方 cookbook（+2% / 幻觉工具警告） |
| S10 | https://github.com/openai/openai-cookbook/blob/main/examples/Orchestrating_agents.ipynb | 官方 cookbook（`routine` / handoff） |
| S11 | https://github.com/openai/openai-agents-python/blob/main/docs/multi_agent.md | 官方文档 |
| S12 | https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md | 官方文档（`input_filter`） |
| S13 | https://github.com/openai/openai-agents-python/blob/main/src/agents/extensions/handoff_prompt.py | 官方源码（`RECOMMENDED_PROMPT_PREFIX`） |
| S14 | https://github.com/openai/openai-agents-python/blob/main/docs/agents.md | 官方文档（dynamic instructions） |
| S15 | https://github.com/openai/openai-agents-python/blob/main/docs/testing.md | 官方文档（`ScriptedModel` / `call.tools` / workflow drift） |
| S16 | https://github.com/openai/openai-agents-python/blob/main/docs/tools.md | 官方文档（agents as tools） |
| S17 | https://langfuse.com/docs/prompts/get-started | 官方文档（版本/label/缓存困惑） |
| S18 | https://langfuse.com/docs/prompt-management/features/guaranteed-availability | 官方文档（`fallback=` / `is_fallback` / 冷启动） |
| S19 | https://docs.langchain.com/langsmith/manage-prompts | 官方文档（commit tags / environments） |
| S20 | https://docs.langchain.com/langsmith/prompt-engineering-concepts | 官方文档（跨职能论据） |
| S21 | https://docs.langchain.com/langsmith/manage-prompts-programmatically | 官方文档 |
| S22 | https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-management.html | 官方文档 |
| S23 | https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-management-deploy.html | 官方文档 |
| S24 | https://docs.langchain.com/oss/python/langchain/context-engineering | 官方文档（dynamic_prompt vs dynamic tools） |
| S25 | https://docs.langchain.com/oss/python/langchain/multi-agent | 官方文档（五模式） |
| S26 | https://docs.langchain.com/oss/python/langchain/multi-agent/subagents | 官方文档（isolated / fork） |
| S27 | https://docs.langchain.com/oss/python/langchain/multi-agent/skills | 官方文档 |
| S28 | https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs | 官方文档 |
| S29 | https://github.com/humanlayer/12-factor-agents/blob/main/content/factor-02-own-your-prompts.md | 社区方法论（**非厂商**） |
| S30 | https://www.promptfoo.dev/docs/configuration/expected-outputs/ | 第三方工具官方文档 |
| S31 | https://pypi.org/project/prompt-contracts/ | 社区项目（**非标准**） |
| S32 | https://developers.openai.com/api/docs/guides/prompting | **站点 403，未一手核验** |
| S33 | https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/generativeaionvertexai-prompt-template-create-generate-save | **站点超时，未一手核验** |

## 本地参考项目（第三部分）

| 仓库 | 关键文件 |
|---|---|
| deepseek-harness | `packages/core/system-prompt/src/index.ts`（614 行 registry + order 表）、`packages/core/system-prompt/README.md`、`packages/preset/persona/src/index.ts:37-60`、`packages/fs/tool-fs/src/read.ts:68-73`、`packages/subagent/tool-subagent/src/index.ts:250`、`.agents/notes/implemented/architecture/2026-07-05-prompt-variables-and-tool-guidance-ownership.md`、`.../2026-08-25-sparse-first-party-prompt-section-orders.md` |
| WeKnora | `docs/agent-prompt-assembly.md`（139 行）、`internal/agent/prompts.go:400-470`、`internal/agent/grounding_prompt.go:17-80`、`internal/agent/prompt_composition_test.go`、`internal/agent/grounding_prompt_test.go`、`internal/application/service/chat_pipeline/prompt_boundary_test.go`、`internal/config/agent_prompts.go:10`、`frontend/src/utils/agentPromptTemplates.ts:15-24`、`config/prompt_templates/*.yaml` |
| claude-code | `src/constants/prompts.ts:259/336/444/728`、`src/constants/systemPromptSections.ts`、`src/utils/systemPrompt.ts:41-131`、`AgentTool/runAgent.ts:891-916`、`AgentTool/forkSubagent.ts:56-70,161-190`、`AgentTool/prompt.ts:14-38`、`src/constants/promptEngineeringAudit.runner.ts`、`docs/context/system-prompt.mdx` |
| codex | `codex-rs/ext/extension-api/src/contributors/prompt.rs:9`、`codex-rs/core/src/context/world_state/persistent_mode.rs`、`codex-rs/core/src/context/update_plan_instructions.rs:4`、`codex-rs/core/src/tools/handlers/multi_agents_common.rs:182`、`codex-rs/core/tests/suite/prompt_caching.rs` |
| financial_rag | `app/prompts/agents/<name>/{agent.yaml,system.md}`、`app/prompts/shared/*.yaml`、`app/prompts/loader.py:61,158,174,312-341`、`app/services/prompt_service.py:20`、`app/models/prompt_version.py`、`app/prompts/shared/tool_fallback.yaml`（**反面案例**） |
| ragflow | `rag/prompts/*.md`（约 45）、`rag/prompts/template.py:11`、`internal/harness/core/prompt_builder.go:19-70,87+`（**未接线**）、`internal/service/kb_prompt.go:61-110` |
| dify | `api/core/prompt/advanced_prompt_transform.py:267`、`api/core/prompt/prompt_templates/advanced_prompt_templates.py`、`api/core/agent/prompt/template.py`、`api/services/agent/prompt_mentions.py` |
| fastapi-langgraph 模板 | `app/core/prompts/__init__.py:13`、`app/utils/graph.py:105,138` |
| langgraph | `libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py:137`（唯一可借点） |
| Qwen-Agent / awesome-llm-apps / fastapi-0.141.1 / full-stack-fastapi-template / fastapi-best-architecture | 无 prompt 管理，快速跳过 |
