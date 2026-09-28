## Why

skill 从"加载"到"派子代理"再到"子代理干活"这条链路上，有四处口径不一致或能力缺失，且都有实机证据：

1. **加载语义与上游相反，且规格与代码互相矛盾**：`context` **未声明取 fork**（`src/agents/skills/loader.py:163`），而 Claude Code 的默认是 inline（官方文档占位符原文 `context: {{inline or fork -- omit for inline}}`）；主规格 `skill-registry` 的「context 值约束」写的是"非法值**按 inline 处理并记 warning**"，代码却是**抛错跳过整个 skill**（`loader.py:164-165`）；未声明的默认值规格里**根本没有规定**。
2. **预加载把 fork 正文注进主 agent**：`_preload_skills_text` 取 `inline_prompt`，为空时**回落 `fork_body`**（`src/services/agent_service.py:897-899`）。`fork_body` 是写给**子代理**的 prompt，却被当作方法论注入主 agent 上下文——与 fork 的"正文不进主 agent"语义直接冲突。
3. **两条委派路径各缺一半**：主 agent 想自己派子代理时，`delegate_task(task, skill)` 的 **`skill` 是必填**（`src/agents/skills/delegate_task.py:38-41`），**无法派通用子代理**——与 Claude Code 的通用 `Agent` 工具、deepseek-harness 的 `{description, prompt}` 型 `subagent` 工具都有实质差距。而子代理的工具面是**零**，通用委派即便做出来也无事可做。此外 fork 子代理的确认标记（`CONFIRM_REQUIRED:`）在**主 agent 委派路径不被剥离**（`strip_confirm_marker` 唯一调用点在 `skill_direct`），会作为内部协议串回到主 agent。
4. **子代理在 Langfuse trace 上是黑盒**：fork 子代理的 LLM 调用与工具调用**均不产生 observation**。`executor.py:189` 的 `var_child_runnable_config.set(None)`（本意只是隔离 SSE，防 token 污染与子代理原文累积）把配置继承整条切断，连带切断了 trace 采集；`ToolTraceCollector` 又只消费**主图**的 `graph.astream_events`（`agent_service.py:723`）。日志侧不缺（`delegate start` / `delegate model turn` / `delegate end` 完整），但 trace 侧看不到子代理检索了什么、拿到了什么。

**上游与外部 skill 事实（本次逐字核对）**：`context: fork` 在 Claude Code 12 个内置技能、219 个用户级技能、CodeBuddy 1092 个 SKILL.md 中**使用数为 0**；官方唯一作者准则是 `Only set context: fork for self-contained skills that don't need mid-process user input.` 本项目 `skills/` 三份 skill 相对上游副本（`~/.agents/skills/`）**只改了 description 一句尾句**，正文逐字相同；其中两份**纯自包含**，只有 `financial-statement-analyzer` 的第一步需要用户输入（上游原文）。

## What Changes

- **`context` 缺省改为 inline**（回归上游 `omit for inline`）；**正文超 `INLINE_PROMPT_MAX_CHARS` 时加载期按长度自动改用 fork** 并记 warning，显式声明永远优先；`SkillRecord` 新增 `context_source` 供排障。
- **统一 `context` 非法值口径**：以代码的"抛错并跳过该 skill"为准（fail loud，与非法 `name` 同款），规格随之改写。
- **预加载只取 inline 正文**：`fork` skill 出现在预设 `skills:` 列表时记 warning 并跳过，不再回落 `fork_body`。
- **`delegate_task` 的 `skill` 参数改为可选**：省略即**通用委派**（不加载任何 skill 正文，用执行者人设 + 任务描述构建子代理，复用 fork 路径的全部控制）。
- **fork 子代理工具面改为"默认继承执行者、声明只用于收窄"，且默认只继承只读工具**：继承面 = 执行者工具面 − 禁用集（`FORK_FORBIDDEN_TOOLS` + 主 agent 专属工具类 `task_*`）− **非只读工具**（依 `readonly_map()`，缺项按非只读处理）；写类工具必须由 skill 在 `allowed-tools` 里**显式声明**才下发（白名单退为例外通道）。通用委派与定点委派**共用同一规则**。
- **委派路径建立子上下文隔离**（工具面继承的**前置条件**）：`delegate_task` 的两个分支 SHALL 各建 `DelegateRun`（子 `RequestContext`）并传入执行器，使子代理检索写**子**引用池、不污染主池；委派状态按 `delegate_id` 分槽，不用 `RequestContext` 单值字段。
- **模型裁量的委派实行会话级预算**：计数器落在**中性进程级单例**（与 `task_registry` 同址），并在会话删除路径显式复位 + 复用 `task_registry` 同款 **TTL 惰性清理**（防进程内 dict 只增不减）；`delegate_task` 超限拒绝并返回可读原因（含"不要再重试委派"）；用户显式 `/xxx` 直出**不消耗**预算；**不设嵌套深度上限**（子代理不持有 `delegate_task`，深度恒为 1）。
- **子代理确认标记在委派路径剥离**：内部协议串不再回给主 agent。
- **子代理在 Langfuse trace 上可见**：把 fork 子代理的工具调用记为同一条 trace 上的 span，挂在标注 `delegate_id` / `skill` 的**委派父 span** 之下。**复用主图工具 span 的同一采集实现**，按既有 `scope` 标识分域（不新建 `DelegateTraceCollector`）；由消费子代理事件流的一方**显式喂事件**驱动，`var_child_runnable_config` 的 SSE 隔离保持不变。
- **MCP 前置约束**：MCP 工具接入时 SHALL 注册进同一工具注册表并**按只读性**进入子代理工具面——**只读 MCP 工具自动进入、写类须由 skill 显式声明**（同内置工具口径）；SHALL NOT 引入"每个 skill 各自声明工具白名单"的机制。
- **明确不做**（评审后否决，理由见 design D4/D12）：不引入 SKILL.md 正文的结构化执行注解（`Execution:` 一族）；不给 `/xxx` 直出加准入守卫；不改任何轮次/时长护栏的值（单开 change）；**不改 `skills/` 下任何文件**。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `skill-registry`: 「SkillRecord 运行时对象」明确 `context` **缺省取 inline**、超 `INLINE_PROMPT_MAX_CHARS` 时自动改用 fork、显式声明优先、非法值抛错跳过，并新增 `context_source`；「正文按 context 解析」与「长内容的建模去向」随之改写（长文与 fork 的绑定是**预算**理由，不构成"长文必须 fork"的规则）。
- `agent-preset`: 「预加载 skill」限定只注入 inline 正文；fork skill 被声明时跳过并记 warning。
- `delegate-task`: 「delegate_task 工具」的 `skill` 改为可选并定义通用委派语义；「fork 执行」的子代理工具集口径改为"默认继承、声明收窄"；「防递归」由"零工具硬保证"改为"禁用集硬保证"；「fork 直出路径」补明"不需准入守卫、中途交互由既有确认门承载"；新增「模型裁量委派的会话级预算」与「子代理确认标记在委派路径不泄漏」。
- `llm-tracing`: 子代理的工具调用记为 trace span（委派域）——复用主图同一采集实现、按 `scope` 分域、显式喂事件驱动，不改 `var_child_runnable_config` 的 SSE 隔离。

## Impact

**代码**

- `src/agents/skills/loader.py` — `_resolve_context` 缺省值与超限自动判定（需把正文长度传入判定）、非法值口径；`_warn_fork_without_tools` 语义失效；**同批修正模块 docstring 与 `const.py` / `models.py` 里"未声明取 fork""超出仅记 warning"三处已说谎的注释**
- `src/agents/skills/models.py` — `SkillRecord` 新增 `context_source`
- `src/agents/skills/fork_tools.py` — `select_fork_tools` 缺省分支、**只读收窄**与交集口径
- `src/agents/skills/delegate_task.py` — `DelegateTaskArgs.skill` 改可选 + 通用委派分支 + **两个分支都建 `DelegateRun`（子上下文隔离）** + 返回前**剥离确认标记前缀但保留问题文本** + 预算检查
- `src/agents/skills/executor.py` — `_fork_tools` 收窄逻辑（含只读收窄与空表 fail-closed）；通用委派无 skill 正文时的输入构造；委派父 span 的开合（含 idle / total / turn / cancelled 中断路径）
- `src/agents/skills/fork_stream.py` — 消费子代理事件流时同时喂给委派域采集器
- `src/services/agent_service.py` — `_preload_skills_text` 只取 inline 正文（移除 `fork_body` 回落）
- `src/chat/` — **会话级委派计数器**（进程级单例，与 `task_registry` 同址同类；不得放 `services/`，否则违反层间规则）
- `src/services/app_service.py` — 会话删除路径（`:244` 的 `clear_history_async`）挂钩计数器复位。**注意：全仓不存在 `/clear` 命令**，不得据此设计复位点
- `src/config/settings.py` — `DELEGATE_MAX_PER_SESSION`
- `src/config/prompts/__init__.py` — `FORK_DEFAULT_EXECUTOR_PROMPT`（`:81-84`）按路径区分"是否标注 `[n]`"（直出路径须标，委派路径不标）
- `src/config/const.py` — 禁用集（含 `task_*`）；`INLINE_PROMPT_MAX_CHARS` 注释改为含"承载方式开关"语义；`DELEGATE_DEFAULT_MAX_TURNS` 注释标注前提已废
- `src/infra/llm/tool_trace.py` — 采集器参数化（`scope` / 父 span / span 名前缀），供委派域复用

**skill / 预设内容**

- **无改动**（`skills/` 下三份 SKILL.md 保持上游原样；D1 的超限自动判定使"手写 `context: fork`"成为不必要）。

**测试**

- `tests/agents/skills/test_skill_loader.py` — 缺省 inline、超限自动 fork、显式优先、非法值跳过、`context_source`
- `tests/agents/skills/test_first_batch_skills.py` — `test_all_inline_skills_within_budget` 语义随自动判定调整；`test_fork_prompt_must_not_mention_tool_names` 前提（零工具）已废，按新语义重定或删除
- `tests/agents/skills/test_fork_tools.py` — 缺省继承、声明收窄、禁用集三档（含 `task_*`）
- `tests/agents/skills/test_fork_executor_selection.py` / `test_fork_sub_agent_contract.py` — 工具面口径变化后的装配；通用委派（无 record）装配
- `tests/agents/skills/test_delegate_budget.py`（新）— 超限拒绝、直出不消耗、会话隔离与重置、深度恒 1
- `tests/services/test_preset_skill_preload.py` — 预加载不注入 fork 正文
- `tests/services/test_agent_service.py` — 通用委派分支与事件可区分；确认标记剥离
- `tests/infra/llm/test_tool_trace.py` — 按 scope 分域、委派父 span 挂载、域间不串台、观测故障不阻断
- `tests/services/test_run_generation_tracing.py` — 主图工具 span 结构不回归

**文档**

- `docs/agents/` — `glossary.md`（补「通用委派」「子代理工具面」「context_source」并修正既有条目）；`logging-rules.md`（新事件与 skip reason）；`api_contract.md`（`delegate_task` 参数契约变更）；`requirements_pool.md`（F-13 / F-34 收口 + 本次登记项）；`data-flow.md`（如涉及 trace 采集链路）
- ADR：本变更含不可逆取舍（`context` 缺省语义、超限自动判定、工具面继承、`delegate_task` 参数放开），按 `docs/adr/README.md` 模板撰写；**编号须查 `dev-wsl` 分支当前最大号**

**依赖与边界**

- **与在途 change 的关系（评审纠正）**：① `turn-provenance-observability` 的**代码大部分已落地**（`STAGE_TURN_AGENT` 已在 `const.py:226`、`_preload_skills_text` 已返回 `tuple[str, list[str]]`、`_resolve_session_agent` 已返回结构化）——不存在"将来同函数冲突"，但该 change 未归档前仍需在其处注明本变更改的是同一函数的**回落分支与跳过语义**；② `skill-external-sources` 与本变更共享 `skill-registry: Skill 文件结构` 这条 requirement → 文本可并存，sync 顺序本变更在先。
- **与在途草案的关系**：`fork-tool-face-by-inheritance`（worktree `corporate_rag-fork-tool-face`，草案未提交 + ADR-0016 Proposed）的**工具面规则**由本变更采纳并落地（并追加"只读收窄"与"委派路径子上下文隔离"两条其草案未覆盖的前置）；该草案执行时应收敛为指向本变更已落地的规则（或归档）。F-34 的 description 尾句处置转交 `skill-external-sources`（本变更遵守"外部 skill 原样"不动它）。
- **单开 change**：轮次/时长护栏（暂名 `agent-round-budget`），apply 须排在本变更之后。
- **不在范围**：不新增任何工具（含文件读取，归 F-12）；不实现 MCP 接入（只写前置约束）；不改 fork 的执行者/模型选择顺序与 `FORK_EXECUTION_CONTRACT` 文本；不改 `turn-provenance-observability` 的 SSE 来源声明契约。
