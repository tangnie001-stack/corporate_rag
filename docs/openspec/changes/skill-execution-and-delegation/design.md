## Context

**现状（已实机核对）**：

- **加载**：`_resolve_context` 未声明取 fork（`src/agents/skills/loader.py:163`），非法值抛 `ValueError` 由 `load_all` 捕获后跳过该 skill（`:164-165`）。主规格 `skill-registry` 的「context 值约束」写的却是"非法值**按 inline 处理并记 warning**"，且**未规定未声明时的默认值**——规格与代码两处不符。
- **预加载**：`_preload_skills_text` 取 `inline_prompt`，为空时**回落 `fork_body`**（`src/services/agent_service.py:897-899`）。fork skill 被写进预设 `skills:` 时，其子代理 prompt 会被注入主 agent 上下文。
- **直出**：`/xxx` 命中 fork → `route_entry` 直达 `skill_direct`（`src/agents/graph/workflow.py:103-111`），主 agent 零 LLM 轮（`tests/agents/graph/test_direct_skill_round.py:133-134`），task 仅 `state.query`（`:335`），子代理零工具（`src/agents/skills/fork_tools.py:24-25`）。**直出轮已有确认门**（`src/agents/graph/skill_direct.py:113-140`：规则检测子代理的确认标记 → 经澄清链路问用户 → 带答复重跑一次）。
- **两条委派路径**：`DelegateTaskArgs.skill` 必填（`src/agents/skills/delegate_task.py:38-41`）→ 无法派通用子代理；skill 侧无结构化流程声明手段。
- **确认标记只在直出路径被剥离**：`strip_confirm_marker` 唯一调用点在 `skill_direct`；主 agent 委派路径会把 `CONFIRM_REQUIRED:` 原样当工具结果交给主 agent。
- **MCP**：未实现。`src/` 内仅 4 处预留注释（`src/agents/tools/registry.py:3,5`、`src/infra/llm/tool_trace.py:8,97`）。
- **规格内部矛盾**：`delegate-task` 的 `fork 执行` 写「allowed-tools 为空则继承执行者预设 tools」，`防递归` 写「工具集恒空」；实现站在后者。
- **轮次护栏**：`MAX_AGENT_ITERATIONS = 5`（主 agent，触顶**只记日志、无兜底**）+ `MAX_DELEGATE_BONUS = 2`（走过委派后上限变 7）+ `DELEGATE_DEFAULT_MAX_TURNS = 5`（子代理，触顶有兜底文案）+ idle/total 兜底。主 agent 与子代理**同类触顶、两种用户后果**。

**外部 skill 的实际形态（逐字 diff `~/.agents/skills/` 上游副本所得）**：

| skill | 本项目相对上游的改动 |
|---|---|
| `financial-statement-analyzer` | **仅 description 加尾句**「材料须由主 agent 预检索一并传入 task」，正文一字未动 |
| `competitive-landscape` | 同一尾句 |
| `market-sizing-analysis` | 同一尾句 + 删了一处多余空行 |

`references/` / `examples/` 逐字相同。三份里**只有 `financial-statement-analyzer` 需要中途用户输入**（上游原文「第一步：确定分析对象 → 与用户确认：1. 公司 — 股票代码或名称」），另两份**纯自包含**。→ 上游分析型 skill 的典型形态是自包含的；`requirements_pool` F-34 立的规矩是**外部 skill 保持上游原样**。

**上游对照（调研结论，详见 memory `context-fork-research.md`）**：

- `context: fork` 在 Claude Code 12 个内置技能、219 个用户级技能、CodeBuddy 1092 个 SKILL.md 中**使用数为 0**；文档占位符原文 `context: {{inline or fork -- omit for inline}}`——**默认是 inline**。
- Claude Code 官方唯一的作者准则：`Only set context: fork for self-contained skills that don't need mid-process user input.`
- 子代理触发范式：claude-code = 声明式为主、模型裁量为辅；deepseek-harness = **模型运行时全权裁量**（`/name` 只注入正文）；WeKnora = **无子代理**。

**约束**：

- 生产单 worker，流式状态在进程内——本变更不引入并发/部署面变化。
- 附随文件读取工具尚未提供（F-12）→ 长文 skill 暂时只能靠 fork 路径承载。
- **外部 skill 保持上游原样**：本变更**不改 `skills/` 下任何 SKILL.md**（正文、frontmatter、description 均不动）。
- 本变更为当前最高优先级；`fork-tool-face-by-inheritance` / `fork-result-and-task-contract` / `one-loop-two-roles` 均为低优先级，本变更不为避让它们而缩范围。

## Goals / Non-Goals

**Goals:**

- skill 的**加载语义**与上游一致（不写即 inline），且**不改动任何外部 skill 文件**。
- 修掉"预加载把子代理 prompt 注入主 agent"的语义错配。
- 确认 `/xxx` 直出的成立条件（已有确认门覆盖中途交互），并消除"直出轮无法与用户交互"的错误认识。
- 补齐两条委派路径：模型裁量可派**通用**子代理；子代理能拿到完成任务所需的**工具面**。
- 让子代理在 **Langfuse trace 上可见**，并修掉确认标记在委派路径的泄漏。

**Non-Goals:**

- **不引入 SKILL.md 正文的结构化执行注解**（`Execution:` 一族）——评估后否决，理由见 D12。
- 不新增任何工具（含文件读取，归 F-12）；不实现 MCP 接入，只写前置约束。
- **不改任何轮次/时长护栏的值**（`MAX_AGENT_ITERATIONS`、`MAX_DELEGATE_BONUS`、`DELEGATE_DEFAULT_MAX_TURNS`、idle/total 兜底）——单开 change（D13）。
- 不改 fork 的执行者/模型选择顺序、不改 `FORK_EXECUTION_CONTRACT` 文本。
- 不改 `turn-provenance-observability` 已定的 SSE 来源声明契约。
- 不做 agent team / `Teammate` 语义。

## Decisions

### D1 `context` 未声明取 **inline**；正文超 `INLINE_PROMPT_MAX_CHARS` 时**按长度自动改用 fork**

- 规则：缺省 inline（`context_source=default`）→ 若正文长度超 `INLINE_PROMPT_MAX_CHARS` 则自动按 fork 处理（`context_source=auto_oversize`）+ warning → 作者**显式声明**时以显式为准（`context_source=explicit`）→ 非法值抛错跳过。
- **理由（三条约束同时满足）**：① 上游默认就是 inline（"omit for inline"），而 `fork` 在真实生态使用数为 0；② **不改动任何外部 skill 文件**——若改成"缺省 inline + 让人手写 `context: fork`"，三份上游 skill 都得改 frontmatter，与"外部 skill 保持上游原样"冲突；③ 长文自动落 fork，F-13 的历史预算问题不回归。
- **代价**：引入一条隐式规则（"太长就自动 fork"）。缓解：记 warning 且 `context_source` 可查，行为可见、可排障。
- **备选**：缺省 inline + 三份 skill 手写 `context: fork` → 否决（改上游文件）；保持缺省 fork → 否决（与上游默认相反，且"不写=换执行载体"是最意外的默认）。
- **连带（必须同批，否则留下一批说谎的注释/文档）**：
  - 代码内：`src/config/const.py:94-96` 对 `INLINE_PROMPT_MAX_CHARS` 的注释现写"超出**仅记 warning**"——D1 使它同时是**切换承载方式的行为开关**；`src/agents/skills/loader.py:8`（模块 docstring）与 **`src/agents/skills/models.py:30`**（`SkillRecord.context` 的字段注释，写着"未声明取 fork"）须一并改。**注意定位**：`SkillContext` 类（`models.py:12-20`）里**没有**这句话，且 `:29` 是 `description` 字段、`:30` 才是 `context`——别改错地方。
  - 文档内：`docs/agents/code-map.md:188`、`docs/agents/api_contract.md:1066`（"allowed 为空→零工具"）、`docs/agents/data-flow.md:81`（"零工具子代理"）同样含旧语义，须随 D7/D8 同批更新（task 10.4 的范围须扩到这三处，不只 trace 链路）。
- **连带（可观测性）**：`context_source` 除落 `SkillRecord` 外 SHALL 进**启动/加载日志**（记 skill 名、`context_source`、正文长度），使"该 skill 为何按 fork 承载"在无人工 E2E 时也可判定。
- **`context_source` 的字段约定（第三轮评审指出，防牵连）**：SHALL 给**默认值**（`"default"`）并**追加到字段列表末尾**——若设为必填或插在中段，会一次性引爆 `loader.py:96` 及测试里约 16 处 `SkillRecord(...)` 构造。消费者侧已核安全：`to_tool_description`（`registry.py:88`）与 `capability_service.py:28` 只取 name/description，`/api/skills` 与前端不受影响。

### D2 `context` 非法值口径统一为"抛错跳过"，改规格而非改代码

- **理由**：代码注释的理由成立——降级到 inline 是静默失效，降级到 fork 会得到行为反转；两者都在猜作者本意。与非法 `name` 同款（fail loud）。
- **代价**：`skill-registry` 主规格那句"按 inline 处理"随之改写（本变更 delta 已含）。

### D3 预加载只注入 inline 正文，fork skill 记 warning 并跳过

- **理由**：`fork_body` 是写给子代理的任务/方法论 prompt；注入主 agent 与 fork 的"正文不进主 agent"直接冲突。需要用 fork skill 时应通过委派触发。
- **备选**：保留回落但改名/加标记 → 否决：注入的内容本身就不该出现在主 agent 上下文里。

### D4 保留 `/xxx` 直出，且**不需要**准入守卫——中途交互由既有确认门承载

- **理由**：直出轮**已有**确认门（`skill_direct.py:113-140`），能问用户一次并带答复重跑。所以"子代理不能中途交互"是**错误认识**（评审中更正）；D7 让子代理拿到工具后，"直出轮无人喂材料"的病灶也消失。此时直出等价于 Claude Code 的 `context: fork`，成立且省一次主 agent 往返。
- **适用判据（不落成硬守卫）**：Claude Code 那条"仅自包含 skill 用 fork"在**上游典型形态上成立**（2/3 份自包含），但**不能做成加载期拒绝**——我们的库里 `financial-statement-analyzer`（**上游原样文件**）第一步就需要用户输入，硬守卫会误杀它，而我们无权改它。
- **先前的错误方案（记录以免重犯）**：曾设计"正文含 `[human]` 注解的 skill 不得 `context: fork`，加载期跳过"。三重错误：误杀上游原样文件；忽略了既有确认门；把"需要中途交互"错误地等同于"不能 fork"。
- **备选**：删除直出 → 否决：会连带废掉 `skill_direct` 的确认门与引用池归属（`delegate-task` 既有 Requirement），收益仅是"与上游形态更像"。

### D5 子代理确认标记在委派路径不泄漏

- **现状**：`strip_confirm_marker` 唯一调用点在 `skill_direct`；主 agent 委派路径把 `CONFIRM_REQUIRED:` 原样当工具结果交给主 agent。
- **决策**：`delegate_task` 返回前剥离该标记行；直出路径沿用既有确认门。
- **理由**：内部协议串不应对任何下游可见；且主 agent 会把它当正文读。

### D6 `delegate_task` 的 `skill` 改为可选：省略即通用委派

- **理由**：主 agent 对"skill 未覆盖的深度任务"目前**无任何**派子代理的手段（`skill` 必填，未命中即报错）。上游两家都是通用派发（CC 的 `Agent` 工具、dsh 的 `subagent` 工具均不绑 skill）。
- **语义**：不加载 skill 正文；执行者人设按"会话选定智能体 > 系统默认"（无 `skill.agent` 参与）；**复用 fork 路径的全部控制**（工具面规则、RequestContext 隔离、引用池归属、`maxTurns`、总时长兜底、取消传播）；委派事件与任务看板条目可区分通用/定点。

### D7 子代理工具面改为"默认继承执行者、声明只用于收窄"，**且默认只继承只读工具**

```
子代理工具面 = 本轮主 agent 启用工具集
               − 禁用集（见 D8）
               − 非只读工具（依 readonly_map()；表中缺项按非只读处理 → 不下发）
               ∩（skill 声明 allowed-tools 时）allowed-tools（显式声明可放行写类工具）
               ∩（执行者预设声明 tools 时）preset.tools
```

- **理由**：白名单语义下每新增一个工具就要改所有 skill（工具数 × skill 数双重增长）；上游把 `allowed-tools` 标为 *Experimental*，Anthropic 自己的 skill-creator 都不写它。已被用户否决过该机制。
- **为什么必须加"只读收窄"这一层**（评审 Blocker）：`derive_invocation_flags` 只按 skill 的 `allowed-tools` 推导双轴，**看不到子代理实际拿到的工具面**（`src/agents/skills/invocation.py`）。若继承面不加只读约束，将来注册一个写类工具后会**自动**流入所有 fork 子代理与 `/xxx` 直出（后者连主 agent 的确认都没有），形成"未经用户确认的写操作"缺口。按只读性收窄后，写权限必须**显式**在 `allowed-tools` 里声明——白名单并未消失，而是退为**例外通道**。
- **空表极性必须写明（第二轮评审指出）**：`derive_invocation_flags` 对**空表**是 fail-open（`invocation.py:33-36` "表为空表示工具尚未注册，无法判断只读性 → 不锁模型端"），而 fork 侧对空表应 **fail-closed**（不下发）。两者极性**相反且都是对的**——双轴空表只是少一层保护，fork 空表若"按只读放行"则等于把写权限下发给子代理。同一张表两个消费者极性不同，须在 spec 与代码注释里写明理由，防后人"统一"。**缺项**（表非空但工具名未命中）两侧一致：都按写类处理（fail-safe）。
- **连带**：`loader` 的"fork 未声明 allowed-tools 即零工具"告警作废；外部 skill 的 description 尾句「材料须由主 agent 预检索一并传入 task」失去前提（处置见 D14）。**引用链的闭合见 D16**（子代理自检索的结论在委派路径不可引用）。

### D15 委派路径必须先建 `DelegateRun`（子上下文隔离）——**D7 的前置条件**

**问题（评审 Blocker，已核实）**：`delegate_task` 的三处调用都是 `executor.execute(record, task)`（`src/agents/skills/delegate_task.py:90,94,135`），**不传 `run`** → `executor.py:177-179` 走 `child_ctx = ctx`（**主 ctx**，其 docstring 亦自述"None 时用当前主 ctx、不隔离"）。于是 `retrieve_kb` 把结果写进**主** `ctx.tool_contexts`（`src/agents/tools/rag_tools.py`）。

**为什么现在才成为问题**：现状子代理零工具，所以这条路径"看起来"没问题；D7 一旦让子代理检索，**模型裁量委派**的子代理检索会污染主引用池 → 主 agent 答案里的 `[n]` 与 `final.citations` 错配（主规格 `delegate-task`「fork 执行」明写"子代理使用独立 RequestContext…不污染主 agent"，「fork 直出路径的引用池归属」也以"主池为子代理检索结果"为前提会算错）。

**同一处还有第二个既有问题**：该路径用 `ctx.delegate_id` / `ctx.fork_stop_reason` 这类**单值字段**承载活跃委派状态（`delegate_task.py:98-99,192-193`），而主规格 `delegate-task`「delegate 并发安全」**明令不得使用 RequestContext 单值字段**。D7 让子代理能跑更久、更容易触发一轮多委派并发，该问题随之放大。

**决策**：**D7 落地前（或同批）**，`delegate_task` 的两个分支（定点 + 通用）SHALL 各自建 `DelegateRun(delegate_id=…, skill_name=…, ctx=main_ctx.child())` 并传入 `executor.execute(record_or_none, task, run)`，与直出路径一致（`skill_direct.py:92-112` 已是这个写法）。

**措辞边界（第二轮评审纠正，务必照此理解）**：隔离的手段是"**每次委派独占一个子上下文 `run.ctx`**"。因此委派状态**写在 `run.ctx` 上是对的**（`executor.py:175` 的 `run.ctx.delegate_id = …`、`fork_stream.py:74` 从 `run.ctx` 读——都发生在子上下文里，天然按委派分槽）；要禁止的是把它写在**主上下文**（`main_ctx`）上。**不得**把要求写成"不得使用 `RequestContext` 单值字段"——那会与既有实现字面冲突，实施者照字面删字段会打断整条管线。

**连带修正（第三轮评审指出，必须同批）**：传入 `run` 之后，**停止原因与 `delegate_id` 的读写来源要一起搬**，否则会产生错误终态：

| 位置 | 现状（写/读主 ctx） | 传入 run 后必须改成 |
|---|---|---|
| `delegate_task.py:98-99` | `ctx.delegate_id = …` / `ctx.fork_stop_reason = None` | `run` 侧（`executor.py:175-176` 已经在这么做） |
| `delegate_task.py:146` | `stop_reason = ctx.fork_stop_reason` | **`run.stop_reason`**（`executor.py:223` 已把 `run.ctx.fork_stop_reason` 回写到 `run.stop_reason`） |
| `delegate_task.py:192-193` | finally 里复位主 ctx | 复位 `run` 侧 |

**若不改 `:146`**：executor 写入的是 `run.ctx.fork_stop_reason`，而 `:146` 读主 ctx 恒得 `None` → `ok=True` 恒成立 → **idle / total / turn 三类中断被误记成 `DONE`、SSE `delegate end` 的 `reason` 为空**。这是"只在传入 run 之后才暴露"的连带后果，属本决策的必改项。

**豁免**：`delegate_task.py:92-94` 存在 `current_request_ctx.get() is None` 的分支（无请求上下文），此时**没有父上下文可 `child()`**，隔离要求不适用，按既有 fail-open 处理并记 warning（不得因此抛错）。

**备选**：只给通用委派建 `run`、定点委派维持 `run=None` → 否决：定点委派同样会污染主池，且两条路径分叉会让"引用池归属"更难推理。

### D16 委派路径的引用链：子代理自检索的结论**不可引用**（与直出路径策略不同）

D7 让子代理能自己检索后，**引用链怎么闭合**必须先定，否则实施者会自己发明：

- **主 agent 委派路径**：子代理可检索补事实，但其检索结果落在**子**引用池、**不回流**主池；子代理**不标 `[n]`**（与本项目既有决策一致：`FORK_DEFAULT_EXECUTOR_PROMPT` 与 `docs/agents/prompt-ownership.md` 的 `output-delegate-citation` 都要求 `[n]` 由**主 agent** 补标）。因此**委派路径的引用完全来自主 agent 自己的检索**；要形成可引用来源，主 agent 须自行检索（或把材料随 `task` 传入让子代理不必重复检索）。
- **`/xxx` 直出路径**：**没有主 agent 补标**，故子代理须**自检索并标注 `[n]`**，其子引用池即本轮引用池（`delegate-task`「fork 直出路径的引用池归属」既有要求）。

**由此暴露一处被 D7 激活的既有矛盾**：`FORK_DEFAULT_EXECUTOR_PROMPT`（`src/config/prompts/__init__.py:81-84`）**无条件**要求子代理"不标注引用编号 `[n]`"，与直出路径的要求相反。此前零工具时二者都不可执行、矛盾不可见；D7 后必须处理——按路径给子代理不同的指示（直出路径明确要求标 `[n]`）。

**传递机制（第三轮评审指出：设计必须给出载体，否则实施者各自发明）**：两条路径**共用同一个执行器**，而 `DelegateRun`（`src/agents/skills/delegate_run.py`）当前没有"本次执行来自哪条路径"的字段。故须在 `DelegateRun` 上新增一个**路径标识字段**（如 `via: "direct" | "delegate"`），由 `skill_direct` 与 `delegate_task` 各自填，执行器据此决定给子代理的 `[n]` 指示。**不得**靠任务文本或正文内容去猜路径。

**两路径策略不同，必须写进 spec 与 glossary**，否则实施者会"统一"它们。

**备选**：子代理检索结果回流主池并重排编号 → 否决：需新机制（跨池编号合并），且与既有"子代理不标 `[n]`"的决策冲突；本变更只做最小闭环。

### D8 禁用集分三档，且**必须显式排除 `task_*`**

- `ask_user` — 语义禁止（子代理不直接与用户交互；需中途交互时由 D4 的确认门承载）。
- `delegate_task` — 防递归（委派深度恒为 1）。
- `task_*`（主 agent 专属工具类）— 角色专属：任务看板是主 agent 的执行面。它们**确实在** fork 工具池里（`build_graph` 把本次启用工具整份灌进 sink，而该列表含 `make_task_tools()`），不排除会被继承下发。
- **理由**：三档理由不同，故分列不合并。

### D9 MCP 前置约束（不实现，只约束）

- MCP 工具接入时 SHALL 注册进同一工具注册表（`tool-registry` 已预留此入口），并**按只读性**进入子代理工具面：**只读 MCP 工具自动进入**（由 D7 的继承规则保证）、**写类 MCP 工具须由 skill 显式声明**（同 D7 对内置工具的口径）。**不得**表述为"MCP 工具自动进入子代理工具面"——那与 D7 默认挡掉非只读工具直接冲突（第三轮评审指出）。
- SHALL NOT 引入"每个 skill 各自声明工具白名单"的机制——那正是 D7 否决的形态，MCP 工具数量会让它爆炸。

### D10 模型裁量的委派实行**会话级预算**；不设嵌套深度上限

- **闸门只覆盖"模型裁量"的委派**（`delegate_task`，含定点与通用）；用户显式 `/xxx` 直出**不消耗**预算（次数由用户与"每轮至多一次"决定）。
- **理由（一手依据）**：CodeBuddy 采用工具形态后被迫补上"每会话 spawn 预算 200 次 + 嵌套深度封顶 5 层"，且其文档明确写着**闸门只对 Agent 工具路径计数，workflow / skill 路径不过闸门**。规律：**调用次数由模型决定的路径必须设闸门，由声明/用户决定的路径不需要**。
- **为什么现在必须有**：D6 放开通用委派后，模型可以**不带 skill**地反复派子代理；现有控制只有单次约束（`maxTurns`、`DELEGATE_MAX_IDLE_S`、总时长兜底），**没有跨轮的会话级上限**。
- **实现要点**：默认值 `settings.DELEGATE_MAX_PER_SESSION`（量级参考 50，须足够宽以免误伤）；计数按会话隔离。
- **计数器位置（评审 Important，已修正）**：**不得放在 `src/services/agent_service.py`**——按 `docs/agents/rules.md` 的层间规则，`services/` 是 `agents/` 的**上层**，`agents → services` 属反向依赖。放**中性位置**：与 `src/chat/task_registry.py` 的 `task_registry` 同址同类（进程级单例，提供 `check_and_incr(session_id)` / `reset(session_id)`）。`src/agents/` 读 `src/chat/` 已有先例（`delegate_task.py:22` 已 import `src.chat.task_registry`），不越界。
- **TTL（第三轮评审指出，防内存泄漏）**：`task_registry` 有 `TASK_TTL_SECONDS = 1800` + `sweep_expired()` 的惰性清理（`src/chat/task_registry.py:18,235`），而预算计数器**只挂会话删除**是不够的——Redis 里的历史 7 天到期**不会**触发本进程的清理，进程内 dict 只增不减 → 长跑单 worker 会持续泄漏。故计数器 SHALL 复用**同款 TTL 惰性清理**（同一量级，或直接复用 `task_registry` 的 sweep 时机）。
- **复位点必须显式挂钩**（否则计数永不复位）。**已核实的实际路径**：会话删除会调 `app_service.py:244` 的 `chat_manager.clear_history_async(session_id)`（`src/chat/manager.py:303`）——复位应挂在这里；**全仓不存在 `/clear` 命令**（第二轮评审纠正：先前写的"/clear"是幻影路径，不得再出现在文档里）。另：进程重启即清（进程内态，符合"生产单 worker、流式状态在进程内"的既有约束）。**不引 Redis 键**，避免为计数增加跨进程一致性问题。
- **超限行为**：拒绝该次委派并返回**可读原因**（提示改用自身能力、**不要再重试委派**），不抛异常、不中断本轮；落 `delegate skip`（`reason=budget_exhausted`）。
- **取消/异常时的计数（第二轮评审的 OQ）**：**不回滚**——已发起过就算数（取消的委派同样消耗真实成本）；须在代码注释与 `logging-rules.md` 写明，避免实施者自行发明回滚语义。
- **不设嵌套深度上限**：禁用集（D8）已使委派深度恒为 1——抄 CodeBuddy 的"5 层封顶"在本项目是无对象的配置。
- **已知不精确处**：`delegate_task` 无法区分"声明驱动"与"自由裁量"（都是模型调用工具），故统一计数——这正是默认值要宽的原因。

### D11 子代理在 Langfuse trace 上可见：**复用 `ToolTraceCollector`，按 `scope` 分域，不新建类**

**问题**：fork 子代理的 LLM 调用与工具调用**均不产生 observation**。`executor.py` / `fork_stream.py` / `delegate_task.py` 零 `langfuse` import（全仓 `@observe` 只有 `agent_node.py:169`、`agent_service.py:552`、离线 eval 三处）；`ToolTraceCollector` 只消费**主图**的 `graph.astream_events`（`agent_service.py:723`），而子代理的事件流由 `fork_stream.py:114` 单独消费且只做正文聚合。

**根因是手段与目标不匹配**：`executor.py:189` 的 `var_child_runnable_config.set(None)` 目标是"防子代理 LLM 事件泄漏到外层 `graph.astream_events`（SSE token 污染 + full_answer 累积子代理原文）"，手段却是**切断整个 config 继承**，把 Langfuse 采集一并切了。

**决策：复用同一个类，用标识区分。** 可复用面占实现绝大部分（`consume` 的事件分派、`_normalize_input/_normalize_output`、`_open_round/_close_round`、按 `run_id` 配对、`close()` 兜底、观测失败只记 warning 的纪律）。需参数化两点：**父 span**（委派域挂一个标注 `delegate_id`/`skill` 的委派父 span）与 **span 名前缀**（`delegate:`）。

**标识来源已现成**：`fork_stream.py:113` 给子代理 `astream_events` 的 config 里已有 `metadata: {"scope": "delegate"}`；`_convert_event`（`agent_service.py:214`）的 `scope`（main/delegate）也是既有概念，且已是 `delegate-progress-observability`「共享事件转换管道带 scope」的规格内容。**复用 `scope`，不新造维度。**

**实例划分（评审纠正）**：**主域每请求一个**；**委派域每次委派一个**（一个委派内可能有多轮工具调用，共用一个采集器实例）。原因：`_round` 是单值状态，若同一实例被多个委派或多个流共用会串台。不可表述为"每请求每 scope 一个"——`scope=delegate` 是被所有委派**共享的取值**，按它建实例等于全部委派共用一个实例，正是要避免的串台。

**必须走"显式喂事件"**，不能走"恢复 callback 继承"（后者正是被切断的东西，会污染 SSE）。

**实测已验证（本变更评审期间用假 chat model 跑通，无网络）**：
- `create_agent` 图的节点为 `model` / `tools`（`langchain/agents/factory.py:1502,1506`）；
- 子代理事件里 `metadata["langgraph_node"] == "tools"` **成立**，且自定义 config 的 metadata 是**合并而非覆盖**（`scope=delegate` 不挤掉 `langgraph_node`）；
- `on_chain_start/end name="tools"` 也在（父 span 开合所需）。
→ `ToolTraceCollector` 的入口过滤**无需放宽**即可直接复用。

**残余**：子代理的**模型轮次**暂不建 trace observation（需引入 `generation` 类型 observation），日志侧仍由 `delegate model turn` 承载（含 model / usage / latency）。列 Open Question。

### D12 **不引入** SKILL.md 正文的结构化执行注解（`Execution:` 一族）

**被评估并否决**。曾设计引入上游那套 `Execution: Direct / Task agent / [human]` 注解（让"某一步要委派""某一步需用户参与"可声明、可评审、可测）。否决理由四条：

1. **`Task agent` 在本库 0 个承载对象**——它只对 inline skill 有效（fork 子代理不持有 `delegate_task`），而库里三份 skill 全是 fork；等于为不存在的使用场景引入语法。
2. **`[human]` 的唯一承载对象是上游原样文件**——唯一需要"中途问用户"的是 `financial-statement-analyzer`，而它是上游原样、且 F-34 刚立规矩**不改造外部 skill**。要加注解就得改它的正文。
3. **上游惯例是自然语言表达流程**（"第一步：确定分析对象 / 与用户确认：1. 公司…"），配合 `FORK_EXECUTION_CONTRACT`（"需要确认就输出标记并停止"）与既有确认门**已覆盖该场景**；引入注解等于把"要改外部 skill"变成一个持续诱惑。
4. 违反项目原则"不做未请求的抽象"。

**评审中被否决的更早方案**（记录以免重犯）：`[human] × context: fork` **互斥守卫**（加载期跳过该 skill）。除上述第 2 条外还有两处错误：忽略了**既有确认门**（直出轮本来就能问用户一次）；把"需要中途交互"等同于"不能 fork"。另曾设计"编排层在委派前代问 + 判据判断 query 是否已含所需信息"——该判据需语义理解，机械规则在真实 query 上必然失效（"腾讯这几年业绩怎么样"含公司名但不含"公司代码"），会退化为"总是问"，得不偿失。

**若将来重开此题**，前提是：出现**我们自有的 inline skill**（`Task agent` 才有载体），或明确决定改造外部 skill。

### D13 轮次/时长护栏单开 change，本变更只登记"前提已废"

`DELEGATE_DEFAULT_MAX_TURNS = 5` 的注释写的是"**fork 零工具**默认 turn 上限（防御）"——D7 一旦让子代理拿到工具，该值的前提即失效。但轮次护栏是**一族互相牵制的量**（主 agent 迭代 + delegate bonus + 子代理 turn + idle/total 兜底，且主 agent 触顶无兜底、子代理触顶有兜底文案），取值需按实测标定（`src/cli/symptom_metrics.py` 已有「iteration limit 触顶率」指标可用），**故单开 change（暂名 `agent-round-budget`）**，其 apply 必须排在本变更之后（标定对象是"带工具的子代理"）。本变更只在 `const.py` 注释里标注前提已废并指向该 change。

### D14 外部 skill 保持上游原样；description 尾句的处置转交

- **本变更 SHALL NOT 修改 `skills/` 下任何 SKILL.md**（正文、frontmatter、description 均不动）。
- `requirements_pool` F-34 的两项残留随之处置：②（未声明 `allowed-tools`）随 D7 **自动失效**（工具面不再依赖它）；①（description 尾句「材料须由主 agent 预检索一并传入 task」）在 D7 之后**失去前提但无害**（与 base 段 `tools-delegate` 语义重叠、属冗余），**为遵守"外部 skill 原样"而不在本变更移除**，转交 `skill-external-sources`（它本就负责"外部来源 skill 落盘后的读取面与校验"，且 `requirements_pool` F-14 也把 `skill-registry` 的措辞改动挂在它那里）。
- **理由**：为了"零改动上游文件"这条一致性，宁可留一句冗余文案，也不破例改外部 skill——破例一次就会有第二次（这正是 F-34 回退的教训）。

## Risks / Trade-offs

- **加载语义反转（BREAKING）**：缺省从 fork 变 inline。缓解：超限自动落 fork 保住长文 skill 的行为；加测试断言"在库 skill 的 `context_source` 分布符合预期"。
- **隐式规则（超限自动 fork）**：行为依赖正文长度，作者可能意外。缓解：warning + `context_source` 可查；显式声明永远优先。
- **确认门是一条脆弱的通道**：靠子代理遵守 `FORK_EXECUTION_CONTRACT` 输出标记（模型可能不遵守），且**单槽 + 一次**。D4 保留直出等于继续依赖它；D5 只修了泄漏、没增强可靠性。若实测发现子代理常不吐标记，需在护栏 change 里一并处理（列 Open Question）。
- **通用委派扩大模型自主权**：成本/时长风险。缓解：D10 的会话级预算 + 既有单次兜底。
- **工具面继承扩大子代理能力面**：安全面变大（将来出现写类工具时更明显）。缓解：禁用集三档；现网工具全为只读（`readonly_map()` 已具备数据）。
- **与在途 change 的关系（评审纠正）**：`turn-provenance-observability` 的**代码大部分已经落地**——`SSEInteractionTexts.STAGE_TURN_AGENT` 已在 `src/config/const.py:226` 且被 `src/services/agent_service.py:665` 使用；`_preload_skills_text` **已经返回 `tuple[str, list[str]]`**（`agent_service.py:876`）；`_resolve_session_agent` **已经返回结构化结果**（`agent_service.py:1042` 已消费 `resolution.source`）。所以不存在"将来同函数冲突"，而是**该 change 的代码已在库里、只有它的 tasks 未勾**。本变更要改同一函数的**回落分支与跳过语义**，与该 change 的返回值签名改动**不重叠**，但实施前应先确认该 change 是否还有未落地的部分（以其代码为准，不以勾选为准）。
- **`skill-external-sources` 的关系**：它要改**同一条 requirement** `skill-registry: Skill 文件结构`（它扩来源、本变更改缺省值与超限规则）→ 文本可并存，sync 顺序需本变更在先。
- **确认门的沉默失败（评审指出，本变更不解决）**：`detect_confirm_request` 只匹配**最终文本行首**的标记（`confirm_gate.py:21-34`），而子代理是否吐标记取决于它是否遵守 `FORK_EXECUTION_CONTRACT`。模型不遵守时，D4 的直出会**基于假设直接作答且无任何提示**（用户看到的是一个自信但未经确认的答案）。D5 只修了泄漏、未增强可靠性；该失败模式已登记，处置归轮次护栏 change 或独立 change。
- **`DELEGATE_MAX_PER_SESSION` 默认值拍不准**：太松无效、太紧误伤。缓解：先用宽默认 + `delegate skip` 观测命中率，再收。

## Migration Plan

- 纯增量 + 一处语义反转（缺省 `context` 含义）；**无 DB 迁移**、无 API 破坏（`delegate_task` 的 `skill` 变可选属向后兼容；`/api/*` 不变）；**不动 `skills/` 内容**。
- **执行分两阶段**（一次评审、一次归档；分界线是"是否改变子代理的能力边界"）：
  - **P1（不改能力边界）**：D1/D2 加载语义 → D3 预加载 → **D15 委派路径子上下文隔离**（含停止原因读写源的连带修正）→ D5 确认标记剥离（剥前缀、留文本）→ D11 委派域 trace。
  - **P2（改能力边界）**：D7/D8 工具面继承 + 只读收窄 + 禁用集 → D16 引用链策略（含 `DelegateRun` 的路径标识）→ D6 通用委派 → D10 会话级预算（含 TTL 清理）。
  - **闸门**：P1 全部条目与 P1 验收通过前不动 P2；P2 开工前做一次**独立**的前提确认（见 tasks §0）。**理由**：两轮独立评审的 4 条 Blocker 全部落在 P2 的半径内，P1 侧零 Blocker——风险量级不同，不该共用同一套验证强度。
- 回滚：`_resolve_context` 改回缺省 fork、移除超限自动判定与预加载跳过、`select_fork_tools` 恢复缺省零工具、`skill` 参数恢复必填、移除委派域采集器、委派路径恢复 `run=None`。无 DB/契约回滚。

## 登记项（不在本变更范围，但本次核查发现）

- **`to_tool_description(max_chars=500)` 会截断** `delegate_task` 的可用 skill 列表（`registry.py:88-100`）——skill 数增长后定点委派会"看不到某些 skill"。
- **三个问用户的渠道只有一个被限流**：`ask_user` 受 `MAX_ASK_PER_TURN` 约束（`ask_tools.py:87-94`），而确认门（`confirm_gate.py`）与 verify 的联网确认（`ask_confirm.py`）直接走 `pending_asks`、不计数。
- **`financial-statement-analyzer` 的悬空引用**：正文「详细公式参见 `references/analysis-methodology.md`」，而子代理**读不到**它（无文件读取工具，归 F-12）——即使 D7 让工具面继承也不含读文件能力。上游原文自带。

## Open Questions

- **`DELEGATE_MAX_PER_SESSION` 的默认取值（Important）**：实施时用真实会话的委派分布标定。**安全默认**：先给一个宽值（如 50）但**把命中次数记全**（`delegate skip` + 计数分布进日志），用一版真实数据再收紧；不要凭感觉拍。
- **单轮并发委派是否封顶（Important）**：D10 只上会话级预算；单轮并发数由 `delegate 并发安全` 的 `asyncio.gather` 决定，无上限。**安全默认**：先不封顶，但在 9.12 的并发测试里记录实际并发分布，需要时再补单轮上限。
- **确认门的沉默失败（Important）**：子代理不遵守 `FORK_EXECUTION_CONTRACT` 时，直出轮会**基于假设直接作答且无任何提示**（`detect_confirm_request` 只匹配最终文本行首标记，`confirm_gate.py:21-34`）。本变更只修了标记泄漏。**安全默认**：登记到 `requirements_pool`（归轮次护栏 change 或独立 change），不在本变更增强可靠性。
- **子代理的模型轮次是否也要进 trace（Important）**：现在只做工具 span，委派父 span 上拿不到 token 与耗时。**安全默认**：接受，父 span 标注"仅工具 span"，模型轮次继续由 `delegate model turn` 日志承载。
- **`_open_round` 在子代理独立图下是否会被重复开合**：评审期实测只覆盖了"事件形状匹配"，未覆盖开合的幂等性。实施时验证（tasks 7.5）。
- 直出路径下"子代理自行检索"与"主 agent 后续轮次"的引用编号是否需要在会话级统一？当前设计是各自成池、当轮组装。
- **`context_source` 是否需要进 SSE 或 `/skills` 面板**：当前只进 SkillRecord 与加载日志。
