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

- 用户可在**新建对话时选择智能体**，选定的智能体作为本会话主 agent 的身份，**会话内不可更改**
- 用户可用 `/xxx` **显式加载 skill**（消息级触发），不影响智能体选择，二者正交；并提供**技能选择器**（输入区下拉）作为按钮式入口，与 `/` 补全共用同一候选
- skill 加载后**持续生效**（进入会话上下文），后续轮次仍受影响
- 智能体内容采用磁盘声明式预设（`agents/<name>.md`），来源可参考 `agency-agents`
- fork skill 的执行者来自会话智能体；fork 子代理**放开 `allowed-tools`**
- **system prompt 三层组装**：人设层可替换（preset），环境约束层（KB 检索纪律 + 引用要求）系统强制叠加
- **能力清单接口**：`skills/catalog.json` + `agents/catalog.json` 供前端选择器与 `/` 补全
- **执行框架对齐主流**：子代理改用 `create_agent`（agent-as-tool），脱离已废弃的 `create_react_agent`
- 双轴调用控制（`user-invocable` / `disable-model-invocation`）+ 默认推导
- SKILL.md 契约对齐主流：删 `thinking` / `max-iterations`；`context` 保持

**Non-Goals:**

- 不做智能体的**中途切换**（明确不可变，新建对话才能换；比 deepseek-harness 的"未产出前可换"更严格）
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

### D1. 智能体是会话级身份，新建对话选定后不可变

智能体在**新建对话时**选择；本次会话内**不可更改**，更换须新建对话。未选择时用默认智能体（通用助手，选择器默认项文案＝「默认」，见 D21）。

**理由**：claude-code `mainThreadAgentType`（会话级、resume 保留）与 deepseek-harness preset（per-session）都是会话级；用户明确要求"不可变"，比 deepseek 的"未产出前可换"更严格，实现更简单（无需处理中途换工具集的一致性）。

**备选**：允许中途切换——deepseek-harness 支持但限"未产出前"（`agent-presets/src/index.ts:630`），因为换 preset = 换工具集会破坏会话一致性。我们选更简单的不变式。

### D2. 选定智能体 = 会话主 agent 的人设（替换 system prompt），不是每轮 fork

选定智能体后，其正文注入主 agent 的 system prompt（注入点 `src/rag/prompt.py:40`），主 agent 的编排骨架（循环 / 工具 / verify / format）不变。

**理由**：会话级下拉框的直觉是"这个对话由谁服务"（像选了客服代表）。业界 `mainThreadAgentType` / `dsh-persona`（"gives one agent its own persona… shadowing the deployment-wide persona for this session"）都是**替换主 agent 身份**，而非每轮委派。每轮 fork 会让主 agent 变纯整合者、交互形态改变、引用来源受限。

**备选**：会话 = 每轮强制 delegate 给该 agent——放弃（改变整个交互形态）。

### D3. 智能体预设契约：`agents/<name>.md`

frontmatter：`name`（缺省用文件名）、`description`（何时使用）、`tools`（可选，缺省继承全部）、`skills`（可选，预加载）、`maxTurns`（可选）；正文 = system prompt（人设）。命名风格 agent frontmatter 用**驼峰**（`maxTurns`），skill frontmatter 用**连字符**（`allowed-tools` 等）——两层风格不同，照抄主流。

**v1 不含 `model`**：本项目模型**不可选**（请求体与前端均无 model 字段），多模型（会话级选模型 / preset 带 model 影响主 agent）留待单独设计——避免引入"会话级换主 agent 模型"的架构改动（现有 `AgentService.__init__` 构造 `self._llm` 后由 `build_graph` 一次性绑定，见 `agent_service.py:611,639`）。

**理由（字段照抄主流）**：对齐 claude-code `AgentDefinitionSchema`（`prompt`/`tools`/`model`/`skills`/`maxTurns`）与 codebuddy `.codebuddy/agents/*.md`（`name`/`description`/`tools`/`model`/`skills`/`maxTurns`/…）。业界 agent 定义**标准带工具集**——"光换人设不换工具，只是换了张脸"。

**分阶段**：v1 先支持 `name`/`description`/`skills`/`maxTurns`；`tools` 字段解析但工具隔离实现与 D7 合并。

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
system prompt = ① 人设层 + ② 环境约束层
  ① 人设层（可替换）
       未选智能体 → PromptManager.get_system_prompt()（现有行为，不变）
       选了智能体 → AgentPreset 正文
  ② 环境约束层（系统强制，不可被智能体覆盖）
       kb_bound     → 检索纪律（先检索后答）+ 引用标注要求（[n]）
       未 kb_bound  → KB_UNBOUND_SYSTEM_PROMPT（禁止检索）
       有 skill 时   → DELEGATE_GUIDANCE_SECTION（委派引导）
  ③ 运行时层（消息级，不属于 system prompt）
       inline skill 内容 / verify 指引 / 时间上下文
```

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

### D19. 能力清单：两份 catalog + 两个接口 + 一致性护栏

```
skills/catalog.json  →  GET /api/skills   （skill 清单：name/description）
agents/catalog.json  →  GET /api/agents   （智能体清单：name/display_name/description）
```
- 两份各自放在**对应内容目录内**（skill 的放 `skills/`、agent 的放 `agents/`），互不耦合
- 后端读文件返回（`capability_service`，按 **mtime 缓存**），api 层只转发（守层间规则）
- 文件缺失 / JSON 损坏 → 返回空列表 + warn（fail-open，不 500）
- **一致性护栏**：启动时对比 catalog ↔ 目录，不一致记 warning（"JSON 有但目录无" / "目录有但未登记"）——防"加文件忘登记"
- 前端智能体选择器读 `/api/agents`；技能 chip 下拉与 `/` 补全读 `/api/skills`（同一候选，见 D21）

### D20. 错误处理总则

- **分界**：内容/配置类 → fail-open（warn + 降级）；安全/权限类 → fail-fast（拒绝）
- **子代理执行失败**：沿用现有 `DelegateStopReason` 词表（`idle`/`total`/`turn`/`cancelled`/`failed`）
- **agent**：未知 → 降级系统默认 prompt + warn；会话已绑且传不同 → **拒绝 400**（不可变）；preset 损坏 → 跳过 + warn；同名 → fail-fast
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
- **[`/` 前缀与自然语言冲突]** → 仅行首 `/` 触发补全；后端解析失败按普通文本处理
- **[删 `thinking` / `max-iterations` 兼容性]** 存量若写 → 忽略并 warn；无现有 skill 使用，实际影响为零
- **[人设质量是最大失败源]** MAST 研究（arXiv 2503.13657）中 Specification 类失败占 41.77% → 重视 `description`（何时使用）质量
- **[R1 引入两套 state 概念]** 主图 `AgentState`（19 字段）与 `create_agent` 内部 state 并存 → 文档写清边界；引用池在 `RequestContext` 而非任一 state，避免误解
- **[catalog 与目录 drift]** 加了内容文件忘登记 catalog（反之亦然）→ **D19 一致性护栏**（启动 warn）
- **[确认回路增加交互轮次]** 子代理请求确认 → 用户答 → 重跑子代理，用户感知为"多一次往返" → 仅在子代理确实无法自行判断时触发（靠子代理 prompt 约束"能自己定的别问"）
- **[prompt 分层的回归风险]** 重构成三层可能悄悄改变"未选智能体"的默认行为 → **测试 T1**：未选 agent 时 `build_system_prompt()` 输出与现状**逐字一致**
- **[R1 的框架依赖]** 选 `create_agent` 意味着跟随 langchain 版本演进（`create_react_agent` 废弃即前车之鉴）→ 版本升级独立成维护事项（本 change 不升级，已装版本即可用 `create_agent`）

## Migration Plan

1. **契约层先行**：loader 支持新字段（双轴、agent 预设）、忽略 `thinking` / `max-iterations`（warn）→ 存量不破坏
2. **工具层**：`ToolEntry` 加 `readonly`（现有工具全 True）
3. **智能体层**：新增 `agents/` 加载/注册表 + `agents/catalog.json`；迁移 `finance-analyst` 人设段 → `agents/finance-expert.md`；`skills/catalog.json`
4. **Prompt 层**：`build_prompt` 改三层组装（人设 + 环境约束）；`INLINE_CITATION_INSTRUCTION` 移入环境约束层；**保证未选 agent 行为逐字不变**
5. **执行层**：fork 改用 `create_agent` + 独立 RequestContext + 按 `allowed-tools` 装配 + 执行者选择；`delegate_id` 分槽（并发安全）；确认门节点
6. **路由层**：`agent` 请求字段 + 会话持久化/不可变校验 + `/xxx` 前缀解析 + 双轴推导 + 候选过滤
7. **接口层**：`GET /api/skills` / `GET /api/agents` + 一致性护栏；前端智能体选择器（知识库右侧）+ 技能选择器（深度思考右侧）+ `/` 补全（走 `frontend-design` skill 按设计稿落地，见 D21）
8. **回滚**：纯新增能力 + 两处字段删除；回滚 = 恢复 `thinking`/`max-iterations` 解析、fork 恢复 `create_react_agent` 零工具、关闭 agent 选择器与两个接口，无数据迁移

## Open Questions

- **确认信号的实现形态**：文本标记（如 `NEED_CONFIRM: ...`）还是**结构化工具**（子代理可调的 `request_confirmation`）？设计倾向结构化（不靠模型写对标记），实现细节待定
- **智能体与 KB 的关系**：智能体能否预绑定 KB（如"财务专家默认绑财务 KB"）？本 change 不做，留待评估
- **`when_to_use` 是否独立成字段**（现塞在 description 括号内）
- **多模型**：会话级选模型 / agent preset 带 `model` 影响主 agent —— 因现有图与 llm 启动期绑定而暂缓，需单独设计
- **`langgraph` 版本升级**（1.2.9 → 1.2.11 等）：非阻塞（`create_agent` 已装版本即有），建议独立维护事项，不混入本 change

（已结案：v1 `tools` 隔离深度 → 见 D3/D7；中途换智能体 → 完全禁止，见 D1；`/` 的中文自然度 → 由技能 chip 下拉提供按钮式入口，见 D21）
