## Context

**现状问题**：skill 机制（`agent-delegation-skills`，已实施未归档）只有**一条触发通道**——主 agent 的 LLM 自己判断是否 `delegate_task`，用户无法指定"就用财务专家"。同时"财务专家"这类内容被错建模：`skills/finance-analyst/SKILL.md`（`context: fork`）的正文是"你是一名资深财务分析师…"，这是**执行者人设**而非方法论——与真正的 skill（如 `financial-statement-analyzer`）混为一谈。

**业界已收敛的模型（调研证据）**：

| 参考 | 会话级身份 | 消息级内容 | 组合机制 |
|------|------|------|------|
| claude-code | `mainThreadAgentType`（`--agent A`，`src/bootstrap/state.ts:197`）+ `AgentDefinitionSchema`（`prompt`/`tools`/`model`/`skills`） | slash skill（`/xxx` → 注入对话消息） | skill `context: fork` + `agent: <预设>`；agent `skills:` 预加载 |
| deepseek-harness | `dsh-agent-presets`（per-session composition：tools + prompt sections + skills）+ `dsh-persona`（会话 system prompt 覆盖） | skill 注入（`source.kind='skill-invocation'`，落成 user/message） | 子代理继承父 preset composition |
| agency-agents | 智能体**人设预设**库（非 skill） | — | — |

**关键结论**：① 选 agent 是**会话级**（三方一致，非每轮）；② agent 定义**标准带工具集**；③ fork 执行者默认继承会话 agent，skill 可用 `agent:` 覆盖（注：③ 的"继承"是我们的**有意偏离**，claude-code 硬默认 general-purpose 不继承）；④ **agent 可预绑定默认 skill**（解决"可发现性"）；⑤ **执行框架**官方推"外层自建 StateGraph + 子代理用 `create_agent` + agent-as-tool"，且 `create_react_agent` 已废弃迁至 `langchain.agents.create_agent`（详见 D16）。

**反方证据（需在设计中缓解）**：选择过载（果酱实验 24 vs 6 种，购买率 3% vs 30%）；"别让用户选，系统该自动路由"；GPT Store / Projects-vs-GPTs 的两层选择困惑。→ 缓解手段见 D2、D12。

**UI 设计产物（已出稿，落位与交互见 D21）**：`docs/design/pages/chat-agent-skill-selector-2026-09-11.md` 规格 + `docs/design/chat-agent-skill-selector-mockup-2026-09-11.html` 可交互预览（基线登记于 `docs/design/MASTER.md`）。

## Goals / Non-Goals

**Goals:**

- 用户可在**新建对话时选择智能体**，选定的智能体作为本会话主 agent 的身份；**首次绑定即固化，之后一律以绑定值为准**（传入不一致不阻断，记 warning，见 D1）
- 用户可用 `/xxx` **显式加载 skill**（消息级触发），不影响智能体选择，二者正交；并提供**技能选择器**（输入区下拉）作为按钮式入口，与 `/` 补全共用同一候选
- skill 加载后**持续生效**（进入会话上下文），后续轮次仍受影响
- 智能体内容采用磁盘声明式预设（`agents/<name>.md`），来源可参考 `agency-agents`
- fork skill 的执行者来自会话智能体；fork 子代理**放开 `allowed-tools`**
- **system prompt 三层组装**：人设层可替换（preset），环境约束层（KB 检索纪律 + 引用要求）系统强制叠加
- **能力清单接口**：`GET /api/skills` / `GET /api/agents` 由 registry 派生（不设 catalog 文件），供前端选择器与 `/` 补全
- **执行框架对齐主流**：子代理改用 `create_agent`（agent-as-tool），脱离已废弃的 `create_react_agent`
- 双轴调用控制（`user-invocable` / `disable-model-invocation`）+ 默认推导
- SKILL.md 契约对齐主流：删 `thinking` / `max-iterations`；`context` 保持

**Non-Goals:**

- 不做智能体的**中途切换**：绑定后传入值一律不生效（不报错、不阻断，只记 warning）；换智能体须新建对话。比 deepseek-harness 的"未产出前可换"更严格
- 不做 `/xxx` 调用智能体（`/` 只命名 skill；智能体走选择器）
- 不做技能市场 / 远程分发
- 不做子代理 RequestContext 的完整多租户隔离（只做支撑 fork 工具不污染主 agent）
- **不引入** claude-code 的 `effort` / `paths`（评估后不采纳，理由见下）

### 评估后不采纳的 claude-code 字段

| 字段 | 语义 | 依赖 | 我们的情况 | 结论 |
|------|------|------|------|------|
| `effort` | 推理强度档位 `low/medium/high/xhigh/max` | 支持 effort 参数的模型 | DashScope 只有布尔 `enable_thinking`；该维度已由请求级 `deep_thinking` 覆盖 | 不采纳（与删 `thinking` 自洽） |
| `paths` | gitignore glob，模型碰到匹配文件才条件激活 skill | 文件读写 + cwd | agent 不操作文件，场景是知识库问答 | 不采纳 |
| `hooks` / `mcpServers` / `memory` | agent 生命周期钩子 / MCP / 记忆 | 对应基础设施 | 暂无 | 不采纳 |

> 注：`agent` 字段**已从"不采纳"翻转为核心**——它是"skill 指定执行者"的组合点（见 D6/D13）。

## Decisions

### D1. 智能体是会话级身份：绑定一次，之后以绑定值为准（不阻断）

智能体在**新建对话时**选择，**首次绑定即固化**（bind-once）；此后每轮一律以会话已绑定值为准。未选择时用默认智能体（通用助手，选择器默认项文案＝「默认」，见 D21）。

**判定规则（4 条）**：

| # | 情况 | 行为 | 日志 |
|---|------|------|------|
| 1 | 尚未绑定 + 传入合法 | **固化**传入值（传入值唯一生效的时刻） | info |
| 2 | 已绑定 + 传入为空 | 静默沿用已绑定值 | 无（正常：刷新 / 旧客户端） |
| 3 | 已绑定 + 传入非空且不同 | **忽略传入值**，用已绑定值继续本轮 | **warning（报警）** |
| 4 | 传入值未注册 | 忽略该值；未绑定时降级系统默认 prompt | warning |

**写入机制 = `bind-if-empty` 原子更新**：`UPDATE sessions SET agent=:name WHERE id=:sid AND agent=''`。`AND agent=''` 保证首次写入者胜、幂等、并发安全；且能覆盖**本功能上线前已存在的会话**（DB 有行、`agent` 为空）——若只依赖 `create_session` 的幂等插入，老会话将永远绑定不上（已存在 → 插入被跳过）。校验与写入都在 `StreamingResponse` 之前完成（流一旦开始，HTTP 状态已发出，无法再改语义）。

**不一致不阻断、只报警的理由**：正常流程下前端是「会话内选择器禁用 + 每轮携带同一值」，**不可能产生不一致**；一旦出现，来源只有前端状态 bug、API/CLI 直连绕过、多端并发操作——均属**非预期且低频**。这类"非法的少量"适合当报警而非业务校验：不打断用户，且在日志里可排查。因此**取消 400 拒绝方案**（eliminated：原设计为 `BusinessError(400)`）。

**理由（会话级）**：claude-code `mainThreadAgentType`（会话级、resume 保留）与 deepseek-harness preset（per-session）都是会话级；用户明确要求"绑定后不再变"，比 deepseek 的"未产出前可换"更严格，实现更简单（无需处理中途换工具集的一致性）。

**偏离声明**：immutability 在本项目是**质量/一致性约束**（防中途换人设导致 system prompt 翻转、预绑定 skill 重复注入），**不是安全边界**——故"放行 + warn"在安全上无暴露面，仅损失"调用方被静默忽略"的可观测性，由 D20 的 `agent_used` 回传补齐。

**备选**：允许中途切换——deepseek-harness 支持但限"未产出前"（`agent-presets/src/index.ts:630`），因为换 preset = 换工具集会破坏会话一致性。我们选更严格的不变式。

### D2. 选定智能体 = 会话主 agent 的人设（替换 system prompt），不是每轮 fork

选定智能体后，其正文注入主 agent 的 system prompt（注入点 `src/rag/prompt.py:40`），主 agent 的编排骨架（循环 / 工具 / verify / format）不变。

**理由**：会话级下拉框的直觉是"这个对话由谁服务"（像选了客服代表）。业界 `mainThreadAgentType` / `dsh-persona`（"gives one agent its own persona… shadowing the deployment-wide persona for this session"）都是**替换主 agent 身份**，而非每轮委派。每轮 fork 会让主 agent 变纯整合者、交互形态改变、引用来源受限。

**备选**：会话 = 每轮强制 delegate 给该 agent——放弃（改变整个交互形态）。

### D3. 智能体预设契约：`agents/<name>.md`

frontmatter：`name`（缺省用文件名）、`description`（何时使用）、`tools`（可选，缺省继承全部）、`skills`（可选，预加载）、`maxTurns`（可选）；正文 = system prompt（人设）。命名风格 agent frontmatter 用**驼峰**（`maxTurns`），skill frontmatter 用**连字符**（`allowed-tools` 等）——两层风格不同，照抄主流。

**v1 不含 `model`**：本项目模型**不可选**（请求体与前端均无 model 字段），多模型（会话级选模型 / preset 带 model 影响主 agent）留待单独设计——避免引入"会话级换主 agent 模型"的架构改动（现有 `AgentService.__init__` 构造 `self._llm` 后由 `build_graph` 一次性绑定，见 `agent_service.py:611,639`）。

**理由（字段照抄主流）**：对齐 claude-code `AgentDefinitionSchema`（`prompt`/`tools`/`model`/`skills`/`maxTurns`）与 codebuddy `.codebuddy/agents/*.md`（`name`/`description`/`tools`/`model`/`skills`/`maxTurns`/…）。业界 agent 定义**标准带工具集**——"光换人设不换工具，只是换了张脸"。

**分阶段与作用域**：v1 先支持 `name`/`description`/`skills`/`maxTurns`；`tools` 字段**解析并生效，但 v1 只约束 fork 子代理**（= 执行者 tools ∩ skill `allowed-tools`，见 D7）——**主 agent 的工具集不变**（D2：会话人设替换不改编排骨架）。即"预设声明 `tools` 用来限制它作为 fork 执行者时能用什么"，不用于裁剪主 agent 的可用工具。若将来要让预设限制主 agent 工具集，单独评估（涉及主 agent 工具绑定与双轴推导的联动）。

### D4. skill 是消息级触发、会话级生效

`/xxx` 触发时，skill 内容作为一条**隐藏消息**注入会话上下文；**后续轮次仍生效**，直到上下文压缩或新会话。

**理由**：claude-code 把它作为 isMeta 消息注入对话、deepseek-harness 注入带 `source` 标记的 user/message——都进入历史，故持续生效。澄清"触发粒度=消息级，生效范围=会话级"。

### D5. `/xxx` 只命名 skill，与智能体解耦

`/` 前缀只解析 skill，不解析智能体；智能体只经选择器（会话级）。

**理由**：用户明确要求职责分离，且这样命名空间不歧义（无需 `/` vs `@` 区分）；与 claude-code（`/skill` 语义）一致。

### D6. fork skill 的执行者 = 会话智能体（skill 可 `agent:` 覆盖）

优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > `general-purpose` 默认。

**理由**：claude-code `context: fork` + `agent:`（默认 general-purpose）；deepseek-harness"子代理继承父 composition"。二者合并即上述优先级——**默认继承，可覆盖**。（注：这一档"继承会话智能体"是我们的**有意偏离** —— claude-code 硬默认 general-purpose、不继承会话主 agent；本项目为体验一致性选择继承。）

**「general-purpose 默认」在本书落地 = 系统默认 system prompt**：本项目无内置 agent 类型注册表，`general-purpose` 指 `PromptManager.get_system_prompt()`（即未选智能体时主 agent 用的那个 prompt），**非新造 prompt**。

**fork 的 prompt 结构（对齐 claude-code `runAgent({agentDefinition, promptMessages})`）**：

```
fork 子代理
  ├─ system prompt ← 执行者（agent preset 正文 / 系统默认 prompt）     人设
  ├─ user message  ← skill 内容（任务/方法论）                        任务
  └─ tools         ← 执行者 tools ∩ skill allowed-tools
```

即**人设来自 agent，任务来自 skill**，二者分层不混。修订 `agent-delegation-skills` 中"子代理 system_prompt = skill 的 agent_prompt"的旧约定。

### D7. fork 子代理放开 `allowed-tools`（修订 `agent-delegation-skills` D7）

fork 子代理工具集 = 执行者预设 `tools` ∩ skill `allowed-tools`；配套**独立 RequestContext**（独立 `tool_contexts` / 引用编号 / `pending_asks`）。

**理由**：零工具是"体验差"的根因（子代理无材料）。D7 当年把独立 RequestContext 标为"已知扩展点"，本 change 兑现。**BREAKING**。

### D8. 双轴调用控制 + 默认由工具 readonly 推导（fail-safe）

`user-invocable`（默认 true）/ `disable-model-invocation`（默认 false），字段名沿用主流。未显式声明时按 `allowed-tools` ⊕ 工具 `readonly` 推导：全只读 → 双通道；含写类 → 默认 `disable-model-invocation: true`。

**理由**：副作用是语义属性，不能靠机器自动识别；用"工具 readonly"当事实来源即可消除"这算不算知识类"的模糊。极性取 fail-safe（含写类默认锁模型端），与 claude-code 的 opt-out 默认相反，需在文档记录。

### D9. 删除 `thinking` 字段

fork 思考模式跟随请求级 `deep_thinking`。理由：零使用；与 `deep_thinking` 重复；非标准字段（claude-code 无、1046 主流样本 0 使用）；删后可移除 `_resolve_fork_llm` 的优先级分支与兜底。**BREAKING**。

### D10. `context: inline|fork` 保持不变

与 claude-code `src/types/command.ts:42-47` 完全一致（inline 为默认），不新增、不改名。

### D11. 两个通道各自做候选过滤

禁用模型端的 skill 从 `delegate_task` 可用列表移除；禁用用户端的从**技能 chip 下拉**与 `/` 补全菜单隐藏。理由：只做运行时拒绝会"看见就想调、调了被拒"白烧一轮。

### D12. 智能体内容来源参考 `agency-agents`，并预绑定默认 skill

`agency-agents`（人设库，19 division / ~274 个 agent md）作为首批智能体预设的内容来源；智能体可用 `skills:` 预绑定默认技能。**理由**：缓解"可发现性"——用户零选择也能用（agent 自带默认技能），`/xxx` 作为高级覆盖。

**预加载机制 = 复用 `/xxx` 的同一条注入路径**：会话已绑定预设且其声明了 `skills:` 时，在**该会话首轮生成前**把每个预绑定 skill 的正文按 `/xxx` 的同一方式作为**隐藏消息**注入会话上下文（随历史持久化，后续轮次同 `/xxx` 一样持续生效），**不写入 system prompt**（与 D17"inline skill 属运行时层"自洽）。仅注入一次（首轮），后续轮次不重复注入——避免每轮累积膨胀。未绑定预设或 `skills` 为空 → 不注入。

### D13. 迁移 `finance-analyst`：fork skill → 智能体预设

`skills/finance-analyst` 的正文是人设，迁移为 `agents/finance-expert.md`；其方法论职责可由 `financial-statement-analyzer` 类 skill 承担。

### D14. delegate 并发安全（修复现有隐性缺陷）

**现状缺陷**：`ToolNode` 对一轮内多个 tool_call 用 `asyncio.gather` 并发执行（`langgraph/prebuilt/tool_node.py:858`），而 `delegate_task` 把 `delegate_id` / `fork_stop_reason` 写在 `RequestContext` 的**单值字段**上（`request_context.py:58`，注释即"当前活跃委派 id"）——并发委派会互相覆盖，导致 SSE 事件串号、任务看板串号、增量投递错乱。

**决策**：把委派状态从 ctx 单值改为**按 delegate_id 分槽**（每次调用局部传递或 `dict[delegate_id]`），task registry / SSE 增量 / `fork_stop_reason` 均按 id 隔离。

**理由**：① 修复隐性缺陷（当前并行即出错）；② 与 D7 的独立 RequestContext 是同一件事的两面；③ 让"一轮多调用 → 并行"成为可靠行为。

### D15. 契约字段与主流对齐（细节）

| 项 | 我们的旧写法 | 对齐后 |
|---|------|------|
| 迭代上限 | skill frontmatter `max-iterations` | **删除**；移到 agent 定义 `maxTurns`（对齐 CodeBuddy/claude-code 位置） |
| `allowed-tools` 格式 | YAML 列表 | **逗号分隔字符串**（对齐主流写法），内部转 list |
| 参数占位符 | skill 正文 `{task}` | **`$ARGUMENTS`**（对齐 claude-code/codebuddy）；无占位符时追加 `ARGUMENTS: <输入>` |
| 命名风格 | 混用 | skill frontmatter **连字符**；agent frontmatter **驼峰** |
| 名称字符集 | 未校验 | **限 ASCII slug `^[A-Za-z0-9][A-Za-z0-9_-]*$`**（skill 与 agent 预设名均适用）：`/xxx` 命令天然是 ASCII 惯例（中文名会让 `/财报分析` 落进"非命令形态"分支、**静默按普通文本处理**），且名称同时是传输值（`agent` 字段）与注册表 key。加载期违反者**记 warning 并跳过**；中文展示需求走 `description` / agent 的 `display_name` |

### D16. 执行框架选型：R1（官方 hybrid）—— `create_agent` + middleware 留口不启用

fork 子代理的执行框架用 **`langchain.agents.create_agent`**（现役，替代已废弃的 `langgraph.prebuilt.create_react_agent`），委托方式为 **agent-as-tool**；**主图保持自建 StateGraph 不变**。

**依据（调研 + 本机验证）**：
- 官方口径：`"Use a custom StateGraph when you need to mix deterministic steps with agentic ones"`、`"you can call a LangChain agent directly inside any LangGraph node"`（官方 multi-agent/custom-workflow 页）
- `create_react_agent` 本机实测已标 `@deprecated`：`"has been moved to langchain.agents. Please update your import to from langchain.agents import create_agent"`
- **未被 middleware 的 state 限制挡住**：曾据 langchain#33217（middleware ⊥ state_schema）判定"必须自建"，**本机源码 + 构造实测推翻**——`_resolve_schemas` 会合并两者，`create_agent(state_schema=..., middleware=[...])` 通过；且我们的引用池在 `RequestContext`（ContextVar）而非图 state，更碰不到

**middleware：v1 传空但保留装配位**。理由：middleware 能覆盖的只有"规则判断 + 回模型重生成"，而我们的**引用池/citations/SSE/预算/保险丝全在它能力外**（见「middleware 覆盖边界」表）；唯一真实收益是 `SummarizationMiddleware`（子代理上下文膨胀），但 v1 子代理受 `maxTurns` + 超时封顶，大概率触发不到。
**何时该开**（写死于此处便于将来判断）：实测发现子代理上下文膨胀 → 加 `SummarizationMiddleware`；网关抖动致子代理失败率上升 → 加 `ModelRetryMiddleware`。

**middleware 覆盖边界（环节级，供将来参考）**：

| 环节 | 进 middleware？ | 原因 |
|---|---|---|
| 校验规则（年份完整性 / 引用存在性 / 拒答检测） | ✅ 规则可复用 | 纯函数 |
| 校验失败 → 注入提示 → 回模型重生成 | ✅ 可表达 | `after_model` + `jump_to="model"` |
| 态分派（kb_id → 态A/态B 两套流程） | ❌ | 图路由，不是"一个时点一段逻辑" |
| 决策化（问用户 → 看历史 → 分支） | ❌ | 多步编排 + 依赖 messages |
| 用户交互 / 跨轮挂起（ask_confirm） | ❌ | middleware 不能挂起等用户 |
| 引用池 / citations 结构 / `_relevant_snippet` | ❌ | 在 ContextVar + 自有数据结构 |
| SSE 事件输出 / 预算复位 / 保险丝 / 观测信号 | ❌ | 输出管道 + 我们的预算与日志体系 |

### D17. system prompt 三层组装（人设可替换，环境约束系统强制叠加）

```
system prompt = ① 人设层 + ② 环境约束层          （③ 运行时层不进 system prompt）
  ① 人设层（可替换）
       未选智能体 → PromptManager.get_base_system_prompt()（Langfuse/兜底原文）
       选了智能体 → AgentPreset 正文
  ② 环境约束层（系统强制，不可被智能体或预设覆盖；本地常量，不走远端 prompt 管理）
       引用标注要求   → INLINE_CITATION_INSTRUCTION
       委派引导       → DELEGATE_GUIDANCE_SECTION（有可用 skill 时）
       kb_bound       → 检索纪律（先检索后答）
       未 kb_bound     → KB_UNBOUND_SYSTEM_PROMPT（禁止检索）
       时间锚点       → 当日日期（系统注入）
  ③ 运行时层（消息级，不属于 system prompt）
       inline skill 内容（含预设预绑定 skill 的注入）/ verify 指引
```

**必须拆出 `get_base_system_prompt()`（关键约束，非可选）**：现状 `PromptManager.get_system_prompt()`（`src/infra/llm/prompt_manager.py:167-183`）在返回前**幂等追加** `INLINE_CITATION_INSTRUCTION` + `DELEGATE_GUIDANCE_SECTION`，再追加当日日期（`_with_current_date`）。若人设层直接复用 `get_system_prompt()`，这三段会**同时留在人设层、又由环境约束层再注入一次**（重复注入），且违反"引用要求归环境约束层"。因此：
- 新增 `get_base_system_prompt()` = 只返回 Langfuse/兜底原文（不含三处追加）；
- 三处追加全部移到环境约束层，**顺序与现状保持一致**（base → 引用指令 → 委派引导 → 日期），保证端到端输出不变；
- 日期留在 system prompt（归环境约束层），不挪到消息级——否则"逐字一致"不可能成立。

**两个调用点都要走同一个 builder**：`src/rag/prompt.py:40`（`build_prompt`，agent_node 在用）与 `:61`（`build_simple_prompt`，当前无调用方）。新增 `build_system_prompt(persona, kb_bound, has_skills)` 统一组装，两处改为调用它。

**T1 重述（原"人设层逐字一致"不可能成立）**：改为**端到端快照对比**——未选智能体时，`build_system_prompt(persona=None, kb_bound=True, has_skills=…)` 产出的 system 段，与重构前 `build_prompt` 的 system 段**逐字一致**（含三段追加与日期）。这样既验证行为不漂移，又不与"把三段移出人设层"自相矛盾。

**理由**：① 人设是"你是谁"，环境约束是"你必守的系统规则"，后者不该由内容作者决定；② **防质量回退**——preset 写漏引用要求会让 `[n]`→citations→溯源的整条链失效；③ 降低 preset 编写门槛（作者只写"我是谁"）。
**归属调整**：`INLINE_CITATION_INSTRUCTION` 从"跟在 base prompt 尾部（可能经 Langfuse 拉取）"移到**环境约束层**（本地常量、不走远端 prompt 管理），避免因远端改动而消失。

### D18. 子代理不直接交互 + 确认门（编排层）

fork 子代理**不持有 `ask_user`**（对齐主流：claude-code 默认从子代理剔除 `AskUserQuestion`）。需要确认时走**编排层确认门**：

```
子代理返回 ─→ 【确认门】节点（规则判断，不调 LLM）
                 ├ 检测到"需确认"信号 → 走 ask_user（复用现有澄清链路）
                 │     → 用户答 → 带答案【重跑子代理】
                 └ 无 → 进 verify
```

**否决"子代理直接跨轮等用户"**：会重演 langgraph#6064（新用户消息按默认路由 → 子代理上下文丢失、从头开始），并撞 fork idle 超时（等待用户=静默=被当 idle 杀）。
**依据**：claude-code `Stop`/`SubagentStop` hook 与 deepseek-harness `agent/turn-stopping` + `steer()` 都是同一模式——**校验/强制继续在编排层，不在模型轮**；且 deepseek 注释明示 `SubagentStop only observes`（子代理停止边界只观察，强制逻辑在父层）。
**本项目的原生等价物**：`verify` 图节点 = `turn-stopping`、`_needs_regenerate` = `steer`——**我们不需要新机制，图框架自带**。

### D19. 能力清单：由 registry 派生，不设 catalog 文件

```
GET /api/skills   ← registry.user_visible()（skill 名 + description）
GET /api/agents   ← registry 全部可加载预设（name + display_name + description）
```

**取消 `skills/catalog.json` / `agents/catalog.json`**。理由：catalog 里的 `name`/`description` 与 `SKILL.md`/`agents/<name>.md` 的 frontmatter **完全重复**，属于项目一贯禁止的"双维护"（对照 `logging-rules.md`："事件全集以 `log_events.py` 为准，本文件不抄录，防双维护"）。保留 catalog 就必须额外养三件套——mtime 缓存、缺项回退目录、启动一致性护栏——而这三件套**全部是为这个重复打的补丁**。取消后这三类复杂度一并消失：新增一个预设只需放一个文件。

- **展示名**：agent frontmatter 增加可选 `display_name`（缺省 = `name`），供选择器显示中文名；`name` 仍是传输值与 key（D1：`agent` 字段传 `name`）
- **响应遵循统一信封**：`ResponseModel(data={"skills": [...]})` / `data={"agents": [...]}`（与 `/auth/verify`、`/sessions/task-status` 等既有的 `data={...}` 同构；既有前端按 `body.code === 'SUCCESS' && body.data` 解析，裸返回 `{"skills":[…]}` 会让菜单恒为空）
- **服务端过滤**：`GET /api/skills` 只返回 `user_visible()`（服务端隐藏 `user-invocable: false`），否则禁用项会出现在菜单里（"看见就想调、调了被拒"，与 D11 冲突）；`GET /api/agents` 天然只含可加载预设
- 列表随 registry 的**懒重载**（文件 mtime 变化）自动更新，无需单独缓存层
- **默认项不入清单**：选择器的首项「默认」由**前端合成**（`value=""`），后端不返回伪项。理由：`value=""` 正好对应"未绑定 → 系统默认 prompt"（D1）的既有语义，零后端分支；后端返回伪项会污染"预设 = 真实文件"的定义
- 前端智能体选择器读 `/api/agents`；技能 chip 下拉与 `/` 补全读 `/api/skills`（同一候选，见 D21）
- api 层只转发（守层间规则）；读取失败 fail-open（返回空列表 + warn，不 500）

### D24. fork 直出路径的引用池归属（citations 不能丢）

`/xxx` 触发的 fork **直出**（D22）时，主 agent 零 LLM 轮、**主引用池必然为空**，而子代理在自己的独立 RequestContext 里检索并写出 `[n]`。若沿用"子代理检索不计入主 agent 引用编号"的旧约定，`format_node` 会因主池为空而把**全部 `[n]` 判为越界丢弃** → `citations: []` → 答案看起来有引用但查不到来源，且误记 `INVALID_CITATION` 信号污染检索质量数据；两条引用护栏又都以"主池有 context"为前提而**静默放行**，没有任何告警。

**决策：fork 直出路径下，本轮 `format` 的引用池 = 子代理的 `tool_contexts`**（子代理池的编号与它的答案天然一一对应，**无需改写答案文本**）。
- 「子代理检索结果不计入主 agent 引用编号」的旧约定**限定作用域**为"**模型自动委派**路径"（那条路径下主 agent 之后自己写答案、自己引用，语义正确）
- 拒绝"把子池并进主池 + 重映射 `[n]`"的做法（备选）：`format` 用的 `\[(\d+)\]` 会连带匹配 `[2024]` 这类年份文本，改写答案会放大这个既有 quirk
- 配套断言：直出路径 `citations` 非空、且不产生 `INVALID_CITATION`

### D25. `/xxx` 前缀的存储与清洗（写时保留、读时清洗）

用户消息中 `/name` 前缀的落库与回灌策略：

| 阶段 | 行为 |
|---|---|
| 落库 | **保留原文**（`/finance-qa 腾讯2024营收和净利润`）——用户发的就是它，回放/审计不丢信息 |
| 历史气泡 | 显示原文（前端零改动，与输入框里可见的 chip 一致） |
| **组装 prompt 时** | 用户消息（当前轮 `query` 与历史 user 消息）SHALL 剥掉 `/name ` 前缀后再转 `HumanMessage` |

**理由**：① 不剥离则模型每轮都看到斜杠命令（可能模仿、或当路径复述），且与 D4 注入的隐藏消息重复（同一 skill 出现"命令 + 正文"两次）；② 若改成"剥离后落库"，则用户输入与存储不一致，且剩余文本为空时撞 `MessageModel.content` 非空约束；③ 清洗规则与 `/xxx` 解析**共用同一个函数**（一处事实来源），避免"能解析但洗不干净"的漂移。前端后续可选增强（不在本 change）：气泡内把 `/name` 渲染成"技能徽标"而非纯文本——依赖"落库保留原文"这一前提。

### D26. `/xxx` 直出路径的图入口分派与 verify 语义

D22 定了"fork 直出、主 agent 0 次 LLM 轮"，但**现有图无法表达**：`workflow.py:86` 是 `builder.set_entry_point("agent")`，而 `agent` 节点每次执行必然调一次 LLM。故必须补三样：

**① 图入口分派**
```
START ──route_entry──┬─ "agent"         （常规轮：opts/普通文本）
                     └─ "skill_direct"  （/xxx 命中 fork skill 的直出轮）
```
- `AgentState` 新增字段承载"本轮直出决策"（解析出的 skill 名 + 任务文本），由 `AgentService` 在初始 state 注入
- 入口改为 `add_conditional_edges(START, route_entry, {...})`；`route_entry` 纯规则判断（无 LLM）
- 新增 `skill_direct` 节点：调用 fork 子代理 → 写 `answer` 与 `tool_contexts`（子代理池，见 D24）→ 边到 `verify`

**② 直出轮的 verify 判据来源**：`verify_node` 与两条引用护栏当前都读**主请求上下文**（`ctx.temporal_years` / `ctx.tool_contexts`），直出轮主 ctx 必然为空 → 年份完整性校验与引用护栏**静默跳过**。直出轮 SHALL 改用**子代理上下文**作为判据来源（`temporal_years` / `tool_contexts`），即"本轮材料来自谁，就用谁的上下文校验"。

**③ 直出轮的重生成目标**：`route_verify` 的 `_needs_regenerate=True` 现路由回 `"agent"`（主 agent）；直出轮的 SHALL 路由回 `"skill_direct"`（重跑子代理，上限 1 次，见 D22）。

**理由**：D22 的用户价值（"我知道要谁来干"→ 不浪费一次主 agent LLM）只有靠入口分派才成立；而"直出省了一轮 LLM"必然意味着"主 ctx 没有材料"，所以校验与重生成的判据必须跟着材料走，否则整套 verify/引用护栏在直出轮退化为空转——这正是 D24 在 `format` 层已修的同一个根因，本决策把它补齐到 verify 层。

### D20. 错误处理总则

- **分界**：内容/配置类 → fail-open（warn + 降级）；安全/权限类 → fail-fast（拒绝）
- **子代理执行失败**：沿用现有 `DelegateStopReason` 词表（`idle`/`total`/`turn`/`cancelled`/`failed`）
- **agent（全部 fail-open，无 400）**：未知 → 忽略 + 降级系统默认 prompt + warn；已绑定且传入不同 → **忽略传入值、按已绑定值继续**（不阻断）+ warning（见 D1）；已绑定且传入为空 → 静默沿用；**预设被删除/改名后重开历史会话** → registry 查不到该名：顶栏灰显原始名、生成用系统默认 prompt、记 warn（不给 500，也不清空会话）；preset 文件损坏 → 跳过 + warn；同名冲突 → fail-fast（配置错误，非运行时）
- **生效值回传（补观测性）**：流事件携带 `agent_used`（与 `model_used` 同层，"本轮实际用了什么"），前端据此纠正顶栏显示。理由：不一致被静默忽略时，调用方无从得知——日志只对运维可见
- **确认回路**：用户拒绝/超时/澄清槽被占 → **基于现有信息出结论 + 显式标注"未经确认"**（比"啥也不给"更有用，且标注保证诚实）
- **重跑子代理上限 1 次**（对齐 `MAX_VERIFY_REGENERATIONS` 思路：一次足够，再多是模型不配合）
- **并发**：多委派 `delegate_id` 分槽；多委派同时请求确认 → 沿用现有 `pending_asks` 单槽保护（第二个按"未确认"处理，不排队不覆盖）

### D21. UI 落位与两个入口的形态

**智能体选择器（会话级）**——新对话页 `kb-bar` 行、**知识库选择器右侧**，与 `kb-trigger` 同构胶囊（`user-round` 图标）；默认项文案 **「默认」**（`--text-secondary`、字重 400，对应未选中＝通用助手），选中后 `--primary` + 字重 500；下拉菜单「选择智能体」= 人像图标 + 名称 + 描述 + 圆形单选点，底部锁提示「选定后本会话内不可更改」；历史对话页**不渲染**，顶栏改只读徽标回显（`智能体:财务专家`）。

**技能选择器（消息级）**——输入区 `composer-bottom` 左侧、**深度思考 chip 右侧**，与 `thinking-chip` 同构 chip（`book-open` 图标）；菜单**向上弹出**（composer 贴底）；**选中即向输入框行首插入 `/name␣` 字面量**，与手输 `/` 完全等价；只列 `user-invocable ≠ false` 的技能。

**两个用户入口共用一份候选**：技能 chip 下拉（可发现性 / 鼠标）与输入框行首 `/` 补全（键盘效率）读同一个 `GET /api/skills`，走同一后端前缀解析——**不新增第二条调用通道**，`skill-invocation` 的语义与后端契约不变。

**理由**：① 两选择器语义不同（会话级 vs 消息级），因此**分置两处**、互不合并；② 智能体紧邻知识库——都是"新建会话时的环境选择"，同一行符合既有 `chat-harness.md` 的"环境选择在输入框上方"；③ 技能 chip 解决 Open Question「`/` 对中文用户是否够自然」——提供按钮式入口，让不会打 `/` 的用户也能选到技能；④ 默认项写「默认」而非「默认助手」，与"未选中＝系统默认"的语义一致，且不为默认态造一个新名词。

**设计产物**（前端实现依据）：`docs/design/pages/chat-agent-skill-selector-2026-09-11.md`（规格）+ `docs/design/chat-agent-skill-selector-mockup-2026-09-11.html`（可交互预览），基线登记于 `docs/design/MASTER.md`。

**前端落地方式**：按 `docs/agents/ui-design-flow.md`，实现时调用 **`frontend-design` skill** 落地视觉与交互，改完用 `playwright-cli` 对照设计稿验证，并同步更新 `MASTER.md` 与 `pages/*`。

### D22. `/xxx` 的执行形态与 LLM 轮次（消息级）

`/xxx` 命中后**不经过"模型判断是否委派"**，直接按其 `context` 执行：

| skill 类型 | 执行路径 | 主 agent LLM 轮次 |
|---|---|---|
| `context: inline`（默认） | 主 agent 携带注入的方法论，**单轮**作答 | 1（正常一轮） |
| `context: fork` | **子代理直出**：直接调 fork 子代理产出结果，主 agent 不先跑一轮 | **0** |

**fork 结果的校验在编排层、不在模型轮**：子代理返回 →（确认门，见 D18）→ **主图 `verify` 节点**（纯规则判断，0 LLM 调用）→ 不通过则重跑子代理，**上限 1 次**（对齐 `MAX_VERIFY_REGENERATIONS` 思路：一次足够）。

**理由**：① 用户用 `/xxx` 是"我知道要谁来干"，让主 agent 再跑一轮决定委派是纯浪费（多一次 LLM + 一次交接）；② 校验本就是规则判断（年份完整性 / 引用存在性 / 拒答检测），D16 的「middleware 覆盖边界」表已确认它属编排层职责；③ 与 D18"校验/强制继续在编排层"一致，无需新机制（`verify` 节点 = `turn-stopping` 的原生等价物）。

**未知 skill 的判定（消歧）**：`/` 开头且后续 token 形如 skill 名（`^/[A-Za-z0-9][\w-]*`）但未注册 → 返回"**skill 不存在 + 可用列表**"（**不静默**当作普通文本）；不以 `/` 开头、或 `/` 后不构成命令形态（如 `/ 今天天气`）→ **按普通文本处理**。理由：用户敲了 `/name` 说明意图明确，静默降级会让他以为 skill 生效了。

**前缀的存储与清洗**见 D25（写时保留原文、组装 prompt 时剥离）。

**fork 直出路径的引用池归属**见 D24（主池为空，须改用子代理池，否则 citations 静默丢失）；**直出路径的图入口分派与 verify 语义**见 D26（入口条件边 + 直出节点 + 校验判据随材料走）。

### D23. `/xxx` 的两个用户入口共用一条通道（不新增调用路径）

技能 chip 下拉（D21）与输入框行首 `/` 补全，读**同一份** `GET /api/skills` 候选、走**同一个**后端前缀解析（D22）。chip 只负责"把 `/name␣` 插进输入框"，**不引入第二条请求参数或执行路径**——`skill-invocation` 的语义与后端契约因此不变。
**理由**：两个入口若各自实现，会出现"菜单候选 ≠ 后端可解析候选"的漂移；共用一条通道让"候选过滤"与"执行"各只有一处事实来源。

## 编写判据

### 判据 1：建模——智能体还是 skill？（易混，先判这个）

| 问 | 是 → |
|---|------|
| 内容是**执行者身份**（"你是谁/怎么说话/能用什么工具"） | **智能体预设**（`agents/`） |
| 内容是**方法论/操作手册**（"这类事怎么做"） | **skill**（`skills/`） |
| 会话级身份、开场选定 | 智能体 |
| 消息级指令、随时可加 | skill |

> 一句话：**智能体是"谁在干活"，skill 是"按哪本手册干"。**

### 判据 2：`context` 选型（inline vs fork）

| 条件 | 选 |
|------|-----|
| 规则/方法论，主 agent 自己就能执行且需全局上下文 | `inline`（默认，不写） |
| 自包含、独立深度任务、中途不需用户输入 | `context: fork` |
| 需独立模型 / 独立 token 预算 / 上下文隔离 | `context: fork` |

> 沿用 claude-code `skillify` 判据：「只为自包含、不需要中途用户输入的 skill 设 `context: fork`」。

### 判据 3：双轴选型（3 问，任一为「是」→ `disable-model-invocation: true`）

| # | 问题 |
|---|------|
| 1 | 会**改变外部状态**吗（写库 / 发消息 / 调外部系统 / 写文件 / 建任务） |
| 2 | 后果**不可撤销**吗 |
| 3 | 成本**显著**吗（长耗时 / 付费 API / 大量 token） |

全「否」→ 知识类 → 双通道。**`/xxx` 是用户的显式授权；模型自动触发等于替用户做授权决定。**

### 判据 4：工具 `readonly` 声明（工具作者）

只读 → `True`；写/改/删/发/外部调用 → `False`；仅写进程内状态（如任务看板）→ 默认 `True`。

### 判据 5：命名

核心字段一律用主流规范名（`name`/`description`/`allowed-tools`/`context`/`model`/`user-invocable`/`disable-model-invocation`/`agent`/`tools`/`skills`），**不自造**；私有扩展须在 design 或 `glossary.md` 标注。

### 判据 6：一段 prompt 该写进 preset 还是环境约束层？

| 问 | 归属 |
|---|------|
| 是"你是谁 / 什么风格 / 擅长什么"（身份） | **agent preset 正文**（人设层） |
| 是"必须遵守的系统规则"（检索纪律 / 引用格式 / 禁用工具） | **环境约束层**（系统强制叠加，不进 preset） |
| 随会话条件变化（kb 绑定 / 有无 skill） | **环境约束层** |
| 每次运行临时注入（verify 指引 / inline skill 内容） | **运行时层**（消息级） |

> 一句话：**作者写身份，系统写规则。** 引用要求这类"没了就质量回退"的约束，绝不能交给 preset 作者。

## Risks / Trade-offs

- **[子代理工具污染主 agent 引用编号]** → 独立 RequestContext；必要时给子代理引用加命名空间前缀
- **[循环依赖]** `make_rag_tools → delegate_task → executor → make_rag_tools` 在放开工具后重现 → 工具工厂注入 / 延迟构造，勿让 executor 直接持有 `make_rag_tools` 实例
- **[选择过载 / 双层困惑]** → 默认智能体 + 选项控制在 5-8 个 + 按"成果/任务"命名（避免人格名重名）+ agent 预绑定默认 skill + 选中后展示"能做/不能做"
- **[默认极性偏离 Claude]** 含写类默认锁模型端 → 文档显式说明；作者要放权须显式 `false`
- **[`/` 前缀与自然语言冲突]** → 仅行首 `/` 触发补全；后端判定：形如命令但未注册 → 提示"不存在 + 可用列表"；不构成命令形态 → 按普通文本（见 D22）
- **[删 `thinking` / `max-iterations` 兼容性]** 存量若写 → 忽略并 warn；无现有 skill 使用，实际影响为零
- **[人设质量是最大失败源]** MAST 研究（arXiv 2503.13657）中 Specification 类失败占 41.77% → 重视 `description`（何时使用）质量
- **[R1 引入两套 state 概念]** 主图 `AgentState`（19 字段）与 `create_agent` 内部 state 并存 → 文档写清边界；引用池在 `RequestContext` 而非任一 state，避免误解
- **[引用链在 fork 直出路径断裂]** 主 agent 零 LLM 轮 → 主 ctx 无材料 → `format` 把子代理的 `[n]` 全判越界丢弃、两条引用护栏与年份完整性校验**静默空转**（都以"主 ctx 有 context"为前提）→ **D24**（format 层改用子代理池）+ **D26**（verify 层判据随材料走、重生成路由回直出节点）+ 断言"直出轮 citations 非空、无 `INVALID_CITATION`、护栏不空转"
- **[alembic 迁移链分叉（既有问题）]** 现存两处 alembic 目录（根 `alembic/` 仅 1 个版本；`src/infra/db/mysql_db/alembic/` 有 3 个）+ `alembic.ini` 指向根目录 + 最近一次变更走手工 SQL → 本 change **走手工 SQL 绕开**（`scripts/migrations/`），分叉本身**登记为独立遗留问题**（修 chain 前须比对线上 `alembic_version` 表），不混入本 change
- **[确认回路增加交互轮次]** 子代理请求确认 → 用户答 → 重跑子代理，用户感知为"多一次往返" → 仅在子代理确实无法自行判断时触发（靠子代理 prompt 约束"能自己定的别问"）
- **[prompt 分层的回归风险]** 重构成三层可能悄悄改变"未选智能体"的默认行为 → **测试 T1**：未选 agent 时 `build_system_prompt()` 输出与现状**逐字一致**
- **[R1 的框架依赖]** 选 `create_agent` 意味着跟随 langchain 版本演进（`create_react_agent` 废弃即前车之鉴）→ 版本升级独立成维护事项（本 change 不升级，已装版本即可用 `create_agent`）

## Migration Plan

1. **契约层先行**：loader 支持新字段（双轴、agent 预设）、忽略 `thinking` / `max-iterations`（warn）→ 存量不破坏
2. **工具层**：`ToolEntry` 加 `readonly`（现有工具全 True）
3. **智能体层**：新增 `agents/` 加载/注册表（含 `display_name`）；迁移 `finance-analyst` 人设段 → `agents/finance-expert.md`；**不新增 catalog 文件**（D19）
4. **Prompt 层**：`build_prompt` 改三层组装（人设 + 环境约束）；`INLINE_CITATION_INSTRUCTION` 移入环境约束层；**保证未选 agent 行为逐字不变**
5. **执行层**：fork 改用 `create_agent` + 独立 RequestContext + 按 `allowed-tools` 装配 + 执行者选择；`delegate_id` 分槽（并发安全）；确认门节点；**fork 直出的引用池并轨（D24）**；**图入口分派 + 直出节点 + verify 判据随材料走（D26）**
6. **存储层**：**手工 SQL 迁移**加 `sessions.agent` 列（`scripts/migrations/<date>-add-session-agent.sql`，与既有实践一致，见 D19/Risks 的 alembic 分叉说明）；`SessionModel` / `ChatRepo`（`create_session` 带 agent、`get_sessions` SELECT 加 agent、新增 `bind_session_agent` 原子更新）/ `PersistenceService` / `ChatManager.save_session_async` 透传；**`SessionItem` 与 `sessions/list` 返回该字段**（前端回显 + 每轮携带的数据源；`sessions/messages` 保持 `data` 为数组不变）
7. **路由层**：`agent` 请求字段 + **`bind-if-empty` 绑定（首次写入者胜）+ 不一致忽略并 warning + `agent_used` 流事件回传**（无 400）+ `/xxx` 前缀解析与执行形态（D22）+ **前缀读时清洗（D25）** + 双轴推导 + 候选过滤
8. **接口层**：`GET /api/skills` / `GET /api/agents`（信封 + registry 派生/过滤，D19）；前端智能体选择器（知识库右侧，默认项前端合成）+ 技能选择器（深度思考右侧）+ `/` 补全（走 `frontend-design` skill 按设计稿落地，见 D21）
9. **回滚**：纯新增能力 + 两处字段删除 + 一列新增；回滚 = 恢复 `thinking`/`max-iterations` 解析、fork 恢复 `create_react_agent` 零工具、关闭 agent 选择器与两个接口；`sessions.agent` 列可保留（无副作用，值为空即沿用旧行为），无数据迁移

## Open Questions

- **确认信号的实现形态**：文本标记（如 `NEED_CONFIRM: ...`）还是**结构化工具**（子代理可调的 `request_confirmation`）？设计倾向结构化（不靠模型写对标记），实现细节待定
- **智能体与 KB 的关系**：智能体能否预绑定 KB（如"财务专家默认绑财务 KB"）？本 change 不做，留待评估
- **`when_to_use` 是否独立成字段**（现塞在 description 括号内）
- **多模型**：会话级选模型 / agent preset 带 `model` 影响主 agent —— 因现有图与 llm 启动期绑定而暂缓，需单独设计
- **`langgraph` 版本升级**（1.2.9 → 1.2.11 等）：非阻塞（`create_agent` 已装版本即有），建议独立维护事项，不混入本 change
- **alembic 迁移链分叉**：两处 `<script_location>` 目录 + 手工 SQL 三种机制并存，`alembic.ini` 指向根目录（详见 Risks）→ 已登记为独立遗留问题（`requirements_pool.md`）
- **`maxTurns` 的系统默认值**：**复用既有常量 `DELEGATE_DEFAULT_MAX_TURNS`**（`src/config/const.py:89`，`executor.py:127` 已在用），**不新建**第二个常量——避免同一语义两处定义；若嫌名字不贴切只做重命名，不新增

（已结案：v1 `tools` 隔离深度 → 仅约束 fork 子代理，见 D3/D7；中途换智能体 → 绑定后以绑定值为准、不一致忽略并 warning（不阻断），见 D1；`/` 的中文自然度 → 由技能 chip 下拉提供按钮式入口，见 D21；`/xxx` 执行形态与未知 skill 判定 → 见 D22；两入口共用一条通道 → 见 D23；清单是否用 catalog → 取消 catalog、由 registry 派生，见 D19；`agent_used` 载体 → 流事件（与 `model_used` 同层），见 D20；直出路径的图入口与 verify 语义 → 见 D26；`maxTurns` 默认值 → 复用 `DELEGATE_DEFAULT_MAX_TURNS`）
