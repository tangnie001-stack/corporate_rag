## 1. 契约层（SKILL.md frontmatter）

- [ ] 1.1 `SkillRecord` 删除 `thinking` 与 `max_iterations` 字段、新增 `user_invocable` / `disable_model_invocation` / **`agent`**；`agent_prompt` **改名 `fork_body`**（`src/agents/skills/models.py`）
- [ ] 1.2 `SkillLoader` 解析：移除 `thinking` / `max-iterations`（忽略并记 warning）、新增双轴字段与 **`agent`**、`allowed-tools` 支持**逗号分隔字符串**、**校验 `name`（含缺省目录名）为 ASCII slug**（`^[A-Za-z0-9][A-Za-z0-9_-]*$`，违反记 warning 并跳过）（`src/agents/skills/loader.py`）
- [ ] 1.3 更新存量 skill frontmatter（如 `skills/finance-qa` 冗余的 `context: inline`），确认无 `thinking` 使用
- [ ] 1.4 更新/删除 `thinking` 相关测试用例（`tests/agents/skills/test_skill_loader.py`、`test_skill_models.py`、`test_skill_executor.py`）

## 2. 工具读写属性

- [ ] 2.1 `ToolEntry` 新增 `readonly: bool = True`（`src/agents/tools/registry.py`）
- [ ] 2.2 各工具注册点声明 `readonly`（现有 retrieve_kb / search_web / ask_user 均为 True）
- [ ] 2.3 补充测试：默认值与显式声明

## 3. 智能体预设层

- [ ] 3.1 新增 `AgentPreset` 数据模型（name / **display_name** / description / system_prompt / tools / skills / **max_turns** / source_path）—— **v1 不含 `model`**（`src/agents/presets/models.py`）
- [ ] 3.2 新增 `AgentPresetLoader`：解析 `agents/<name>.md`（frontmatter 用**驼峰**键如 `maxTurns` + 正文 system prompt，fail-open）；**校验 `name`（含缺省文件名）为 ASCII slug**（违反记 warning 并跳过）；`display_name` 缺省 = `name`
- [ ] 3.3 新增 `AgentPresetRegistry`：按名索引 + 懒重载 + 同名 fail-fast
- [ ] 3.4 `AgentService` 装配 presets（`agents/` 缺失/空时记 warning 并降级默认）——**由 Plan 2 提前完成**
- [ ] 3.5 内容迁移：`skills/finance-analyst` 的**人设段** → `agents/finance-expert.md`（参考 `agency-agents` finance 域，仅取身份/使命/规则，中文化裁剪；`display_name: 财务专家`）；原文件**改写为方法论** skill（去掉"你是一名…"）
- [ ] 3.6 新增测试 `tests/agents/presets/`：加载 / 注册 / 冲突 / 缺省继承 / **非法名称跳过**
- [ ] 3.7 **不新增 catalog 文件**（design D19）：清单由 registry 派生；`display_name` 归属 frontmatter

## 4. fork 执行放开工具（修订 D7）

- [ ] 4.1 为 fork 子代理建立**独立 RequestContext**（隔离 `tool_contexts` / 引用编号 / `pending_asks`），并把委派状态从 ctx 单值改为**按 `delegate_id` 分槽**（修 `request_context.py:58` 单值并发缺陷）
- [ ] 4.2 `SkillExecutor` 改用 `langchain.agents.create_agent`（替代废弃的 `create_react_agent`）：system prompt = 执行者人设（agent preset 正文 / 系统默认 prompt）、user message = skill 正文（`fork_body`）、tools = 执行者 `tools` ∩ allowed-tools（空则零工具；**预设 `tools` 仅在此路径生效，不影响主 agent**）；`middleware` 参数**保留装配位但传空**
- [ ] 4.3 执行者选择顺序：skill `agent:` > 会话选定智能体 > **系统默认人设**（`build_system_prompt(persona=None, …)` 组装出的默认 prompt，非新造 general-purpose）
- [ ] 4.4 简化 `_resolve_fork_llm`：移除 `record.thinking` 优先级与 `no_main_model_name_thinking_fallback`，思考跟随 `ctx.deep_thinking`；子代理最大轮次改取执行者 `maxTurns`（**未声明时复用既有常量 `DELEGATE_DEFAULT_MAX_TURNS`**，`src/config/const.py:89`，不新建常量）
- [ ] 4.5 打破循环依赖（工具工厂注入 / 延迟构造，勿让 executor 直接持有 `make_rag_tools`）
- [ ] 4.6 新增**确认门**节点：规则检测子代理返回的"需确认"信号 → 走 `ask_user`（复用澄清链路）→ 答复后重跑子代理（上限 1 次）；无信号直通 verify；被拒/超时/槽被占 → 出结论 + 标注"未经确认"——**移出 Plan 2 → Plan 3**
- [ ] 4.7 测试：工具隔离不污染主 agent、执行者选择优先级、thinking 移除后行为、**一轮多委派并发不串号**、确认门四个分支、用的是 `create_agent` 而非 `create_react_agent`、**入口分派（命令行 → skill_direct，普通文本 → agent）**、**直出轮 verify 判据来自子代理上下文且护栏不空转**（确认门四个分支的测试随 4.6 进 Plan 3）
- [ ] 4.8 **预设预绑定 skill 预加载**：会话已绑定预设且声明 `skills:` 时，在该会话**首轮生成前**按 `/xxx` 同一路径注入一次（隐藏消息，不进 system prompt），后续轮次不重复注入
- [ ] 4.9 **fork 直出的引用池并轨**（design D24）：`/xxx` 触发 fork 直出时，把子代理的 `tool_contexts` 作为本轮 `format` 的引用池（主池此时必为空），使 `citations` 正常产出、不误记 `INVALID_CITATION`；「不计入主编号」的旧约定限定为模型自动委派路径——**已由 Plan 2 完成**
- [ ] 4.10 **图入口分派（design D26）**：`AgentState` 新增"直出决策"字段（解析出的 skill 名 + 任务文本）；`workflow.py:86` 的 `set_entry_point("agent")` 改为 `add_conditional_edges(START, route_entry, {...})`，`route_entry` 纯规则判断（无 LLM）——**已由 Plan 2 完成**
- [ ] 4.11 **直出节点 + verify 语义适配（design D26）**：新增 `skill_direct` 节点（调用 fork 子代理 → 写 `answer`，并把子代理的 `tool_contexts` 写入 **`AgentState` 新增字段**（同时承载 `temporal_years`）→ 边到 `verify`）；材料判据统一由 `AgentState` 承载：`tool_contexts` 复用 + `verify_temporal_years` 新增，流程字段（`web_confirmed`/`verify_ask_count`/`web_guided`）仍读主 ctx（它们在同一次 verify 内被写后读，快照化会改行为）；`route_verify` 的 `_needs_regenerate` 在直出轮路由回 `skill_direct`（重跑上限 1 次）；**与确认门互斥**：确认门不通过时直接出结论+标注"未经确认"，不再进入 verify 重跑——**已由 Plan 2 完成**；直出轮的回答交付/落库由 5.12 承接（本计划未覆盖）

## 5. 调用控制与 `/xxx` 路由

- [ ] 5.1 双轴默认推导：按 `allowed-tools` ⊕ 工具 `readonly` 计算（fail-safe），加载期护栏 warn（含写类未显式声明 / 死 skill）
- [ ] 5.2 注册表提供 `user_visible()` / `model_visible()` 两个候选列表
- [ ] 5.3 `delegate_task` 按 `model_visible()` 过滤工具 description 可用列表
- [ ] 5.4 `/xxx` 前缀解析（`agent_service` 生成入口）：命中则注入隐藏消息 + 跳过模型委派判断；`/` 后剩余文本按 `$ARGUMENTS` 注入 skill 正文（无占位符时追加 `ARGUMENTS: <输入>`）；**执行形态分派**——`inline` 走主 agent 单轮；`fork` **子代理直出（主 agent 0 次 LLM 轮）**，其结果交主图 `verify` 节点校验（0 LLM），不通过最多重跑子代理 1 次；**前缀读时清洗**（design D25）：当前轮 `query` 与历史 user 消息在组装 prompt 前剥掉 `/name ` 前缀，清洗与解析共用同一函数（落库仍保留原文）
- [ ] 5.5 未命中判定：`/` 开头且形如 skill 名但未注册 → 返回"不存在 + 可用列表"（**不静默**）；不以 `/` 开头或 `/` 后不构成命令形态 → 按普通文本；文案入 `SSEInteractionTexts`（`src/config/const.py`）
- [ ] 5.6 请求契约新增 `agent` 字段（`agent: str = ""`，空＝沿用）（`src/api/model/request.py`、`src/api/chat.py`）
- [ ] 5.7 **会话智能体存储链（M1）**：
  - [ ] 5.7.1 **手工 SQL 迁移**加 `sessions.agent` 列（`scripts/migrations/<date>-add-session-agent.sql`，`ALTER TABLE sessions ADD COLUMN agent VARCHAR(64) NOT NULL DEFAULT ''`，附执行说明，与既有 `2026-08-31-add-message-status.sql` 一致；**不走 alembic**，理由见 design Risks/alembic 分叉）；`SessionModel` 加字段（`src/infra/db/models/chat.py`）
  - [ ] 5.7.2 `ChatRepo`：`create_session` 带 agent；`get_sessions` SELECT 加 agent；新增 `bind_session_agent`（`UPDATE … SET agent=:v WHERE id=:sid AND agent=''`，只写一次）
  - [ ] 5.7.3 `PersistenceService.save_session` / `ChatManager.save_session_async` 透传 agent
  - [ ] 5.7.4 `SessionItem` 加 `agent`；**`sessions/list` 返回该字段**（前端回显 + 每轮携带的数据源）；**`sessions/messages` 保持 `data` 为数组不变**（不改其契约）
- [ ] 5.8 **会话智能体绑定与沿用（bind-once，无 400）**：服务层 `resolve_session_agent(session_id, requested)`——校验只查注册表；未绑定 + 合法 → `bind_session_agent` 固化；已绑定 → 一律用绑定值（传入空＝静默沿用；传入非空且不同＝**忽略 + warning**，不拒绝）；传入未注册 → 忽略 + warning；绑定校验与写入**在 `StreamingResponse` 之前**完成；**`agent_used` 走流事件回传**（与 `model_used` 同层；**语义 = 本会话绑定智能体名**，不含 fork 执行者）；已删除预设的历史会话 → 降级默认 prompt + warn
- [ ] 5.9 **system prompt 三层组装**：`PromptManager` 拆出 `get_base_system_prompt()`（不含系统追加段）；新增 `build_system_prompt(persona, kb_bound, has_skills)`（`src/rag/prompt.py`）——①人设层（preset 正文 / `get_base_system_prompt()`）+ ②环境约束层（引用指令 + 委派引导 + 检索纪律 + `KB_UNBOUND_SYSTEM_PROMPT` + 日期，**顺序与现状一致**）；`build_prompt` 与 `build_simple_prompt` **两个调用点**都改走它；**保证未选 agent 时 system 段端到端逐字不变**
- [ ] 5.10 **能力清单服务 + 接口**（design D19：**不引入 catalog 文件，由 registry 派生**）：`src/services/capability_service.py`（取 registry 的 `user_visible()` / 全部可加载预设；随懒重载自动更新，无独立缓存层）；`src/api/capabilities.py`（`GET /api/skills` / `GET /api/agents`，经 service 不直接读文件/扫描目录）；**响应走统一信封**（`data.skills` / `data.agents`）；skills 服务端过滤 `user-invocable: false`；读取失败返回空列表 + warn（不 500）
- [ ] 5.11 测试：前缀路由 / **前缀清洗（当前轮与历史都不含 `/name`，落库仍为原文）** / **`/xxx` inline 单轮与 fork 直出（断言主 agent 0 LLM 轮 + citations 非空 + 无 `INVALID_CITATION`）** / 注入后持续生效 / 双轴过滤 / **绑定四态（首轮绑定、沿用、忽略+warn、未注册降级）** / 老会话 `bind-if-empty` 可绑定 / `agent_used` 流事件 / **未选 agent 时 system 段快照逐字一致** / 两接口信封结构 + 服务端过滤 + 失败降级 / **非法名称跳过** / `sessions/messages` 契约不变（`data` 仍为数组）——**已由 Plan 3 完成**（各断言落点逐条核对见本 change 收口报告 task-10-report.md）

- [ ] 5.12 **直出轮 answer 交付链路（Plan 3 前置，最终评审登记）**：让直出轮的回答经 SSE 交付并被落库。候选做法（择一，实现时定）：在 `_convert_event` 增加 `on_chain_end name=="skill_direct"` 捕获 `answer` 并产出 token/answer 事件；或让 `serialize_process` 在无 `"token"` 段时回落到直出节点的 `answer`；同步更新 `_run_generation` 的 `full_answer` / `capture.final_answer` 捕获源。**必须有一条走生产交付链（`astream_events` → `_convert_event` → `serialize_process`）的端到端测试**——当前直出测试全走 `graph.astream(stream_mode="updates")`，完全绕过交付层，是本次测试策略的系统性盲区。依据：`design.md` D22/D26；最终评审发现直出轮回答既不进 SSE token 流也不落库（缺口登记，本计划未覆盖）
- [ ] 5.13 **I1（本计划欠项，随 5.12 落直出路径前必须解决）**：直出轮 `_needs_regenerate` 重跑不消费 verify 注入的 `SystemMessage` 指引（直出节点不读 `state.messages`）→ 无效重跑；不解决则直出轮的质量纠偏整条失效
- [ ] 5.14 **I2（本计划欠项）**：生产环境 `agents/` 与 `skills/` 未挂载未 COPY（`Dockerfile` 只 `COPY src/ scripts/ deploy/`；`docker-compose.prod.yml` 只挂 `./skills`）→ 生产 `_resolve_executor` 恒 None、会话智能体能力静默降级；部署任务须同时落 `docker-compose.prod.yml` 挂载 + `Dockerfile` COPY

## 6. 前端

> **执行方式（本节所有任务适用）**：前端改动（`deploy/nginx/html/chat.html`）统一**调用 `frontend-design` skill 落地**——视觉方向、排版、交互动效按 `docs/design/pages/chat-agent-skill-selector-2026-09-11.md` 规格与 `docs/design/chat-agent-skill-selector-mockup-2026-09-11.html` 预览实现（落位与交互依据见 design D21）；改完用 `playwright-cli` 对照设计稿验证。

- [ ] 6.1 新建对话页智能体选择器：**知识库选择器右侧**同一行，与 `kb-trigger` 同构胶囊（`user-round` 图标 + 当前名 + chevron）；菜单 = **前端合成的首项「默认」（`value=""`）** + `GET /api/agents` 的 `data.agents`（显示 `display_name`，value 用 `name`），下拉单选（人像图标 + 名称 + 描述 + 圆形单选点 + 底部锁提示「选定后本会话内不可更改」）（`deploy/nginx/html/chat.html`）
- [ ] 6.2 技能选择器：**深度思考 chip 右侧**同构 chip（`book-open` 图标 + 文案「技能」+ chevron），菜单**向上弹出**，选中后向输入框行首插入 `/name ` 并置 chip 选中态（显示技能名），菜单首项「不使用技能」，只列 `user-invocable ≠ false`；新对话页与历史对话页均提供，数据源 = `GET /api/skills`
- [ ] 6.3 输入框 `/` 命令补全（行首触发、按名过滤、插入 `/name ` 字面量、隐藏 `user-invocable:false`），与 6.2 共用候选数据与后端解析，数据源 = `GET /api/skills`
- [ ] 6.4 会话内禁用更换智能体 + 历史对话页顶栏只读徽标回显智能体名（取 `sessions/list` 项的 `agent`；历史页不渲染智能体选择器；预设已删除时降级显示原始名）
- [ ] 6.5 流式请求体携带 `agent`：**取会话绑定值**（新会话取选择器值；恢复会话取 `sessions/list` 项的值），而非当轮 UI 状态；收到流事件 `agent_used` 与显示不一致时静默纠正标识（**不阻断、无 400 分支**）
- [ ] 6.6 两个选择器的无障碍与互斥：`role="button"` + `aria-haspopup="listbox"` + `aria-expanded`；键盘（Enter/Space 开合、↑/↓ 移动、Enter 选中、Esc 关闭并归还焦点）；焦点环可见；与引用抽屉 / 任务面板互斥单开
- [ ] 6.7 落地后按 `docs/agents/ui-design-flow.md` 同步 `docs/design/MASTER.md` 与 `pages/chat-agent-skill-selector-2026-09-11.md`（保证设计与实现一致），用 `playwright-cli` 验证

## 7. 契约与文档同步

- [ ] 7.1 `docs/agents/api_contract.md`：新增 `agent` 请求字段语义（**值为预设 `name`（英文 slug），非 id**；空＝沿用；不一致**不报错**、以服务端已绑定值为准）、`/xxx` 前缀语义与踩坑（含"落库保留原文、组装 prompt 时剥离"）、**`sessions/list` 新增 `agent` 字段**（`sessions/messages` 契约不变）、流事件 `agent_used`——**已由 Plan 3 完成**（落为 api_contract.md「5.6 会话智能体与 `/xxx` 契约」）
- [ ] 7.2 `docs/agents/glossary.md`：新增「智能体预设」「会话级 vs 消息级」术语——**已由 Plan 3 完成**（此前已在 glossary.md「智能体预设与调用控制」登记，收口核对无需新增）
- [ ] 7.3 `docs/agents/code-map.md`：登记 `agents/` 内容目录与 `src/agents/presets/`——**已由 Plan 3 完成**（`agents/` 与 `src/agents/presets/` 已登记；收口补 `prefix.py` / `capability_service.py` / `capabilities.py`）
- [ ] 7.4 `CLAUDE.md`：目录结构速览补 `agents/`；文档组织表按需登记——**已由 Plan 3 完成**
- [ ] 7.5 修订 `agent-delegation-skills` 的 D7 说明（标注被本 change 修订）——**已由 Plan 3 完成**（D7 加「已被修订」标注，指向本 change 的 design D6/D7）
- [ ] 7.6 `data-flow.md`：补「答案校验与引用格式化链路」一节（**本 change 筹备期已完成**，落地时核对与最终实现一致）——**已由 Plan 3 完成**（核对对齐：`state.tool_contexts` / `state.verify_temporal_years` 承载与直出轮判据来源）
- [ ] 7.7 `reference-projects.md`：更新 agency-agents 条目（作为智能体预设来源）+ 记录本次调研结论（R1 执行框架、`create_react_agent` 废弃）——**已由 Plan 3 完成**（langgraph 条目补 `create_react_agent` → `create_agent` 结论）
- [ ] 7.8 `docs/agents/logging-rules.md` + `src/core/log_events.py`：登记本次新增事件（前缀沿用已有 `[agent]`）——`agent mismatch ignored`（已绑定但与传入不同，warning 报警）、`agent unknown fallback`（未注册，warning）、`agent bind`（首轮绑定，info）等；按"开放登记制"先登记 `Event` 枚举 + `EVENT_SPECS` 再启用
- [ ] 7.9 `docs/agents/defensive-patterns.md`：在「并发」分区登记本次缺陷类别——**每请求上下文的活跃状态必须按 id 分槽**（现象：一轮多委派被 `asyncio.gather` 并发调度，而 `RequestContext` 用单值字段承载活跃 `delegate_id`，互相覆盖导致 SSE/看板串号）
- [ ] 7.10 `docs/agents/requirements_pool.md`：登记**独立遗留问题**——alembic 迁移链分叉（根 `alembic/` 仅 1 个版本 vs `src/infra/db/mysql_db/alembic/` 3 个版本，`alembic.ini` 指向根目录，另有手工 SQL 约定），需先比对线上 `alembic_version` 再决定归并方案（**不在本 change 修**）

## 8. 验证与收尾

- [ ] 8.1 `pytest tests/ -v` 全绿
- [ ] 8.2 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 8.3 `python -m src.cli.check_docs` 0 error
- [ ] 8.4 手工 E2E：新建对话选智能体 → 用技能选择器选技能 → 发消息 → 第二轮仍受 skill 影响 → 刷新后顶栏仍回显智能体（值来自 `sessions/list`）
- [ ] 8.5 手工 E2E：一句话触发多个可并行委派 → 观察并发执行且事件/看板按 `delegate_id` 不串号
- [ ] 8.6 **两个 compose 文件**都补 `agents/` volume 挂载（同 `skills/`）：`docker-compose.override.yml`（dev）+ `docker-compose.prod.yml`
- [ ] 8.7 清理死代码：`src/api/chat.py` 的 `get_query_biased_snippet` / `_build_highlighted_snippet` 及随之不再需要的 `jieba` import（两者无任何调用方；归档计划 `2026-07-23-rag-orchestration-phase2` 曾记录应删除但未删）
- [ ] 8.8 手工 E2E：会话**绑定 KB 且选定智能体** → 答案仍带 `[n]` 引用（验证环境约束层未被 preset 覆盖）
- [ ] 8.9 手工 E2E：子代理中途请求确认 → 弹澄清卡 → 答复 → **继续完成**（不从头上重来）
- [ ] 8.10 手工验证 `scripts/migrations/<date>-add-session-agent.sql` 可重复执行/幂等说明（列已存在时的处理）；**上线前已存在的会话**首次携带 agent 能成功绑定（`bind-if-empty`）
- [ ] 8.11 手工 E2E：绑定后请求携带不同 agent（或直连 API 传错）→ 本轮仍按绑定值生成、**不报错**，日志出现 `agent mismatch ignored` warning，前端标识被 `agent_used` 纠正
- [ ] 8.12 手工 E2E：会话绑定 KB + 选定智能体，用 `/xxx` 调一个 `context: fork` skill → 答案的 `[n]` 有来源横条、抽屉可打开（验证 D24 引用池并轨）；日志无 `invalid_citation`
