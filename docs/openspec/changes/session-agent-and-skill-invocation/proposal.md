## Why

当前 skill 机制只有**一条触发通道**——主 agent 的 LLM 自己判断要不要 `delegate_task`。用户想指定"就用财务专家"没有表达方式；而"财务专家"这类内容其实是**执行者人设**（智能体预设），被错放进 `skills/<name>/SKILL.md`（`finance-analyst` 的正文是"你是一名资深财务分析师…"），与真正的 skill（方法论，如 `financial-statement-analyzer`）混为一谈。

业界（claude-code 的 `mainThreadAgentType`、deepseek-harness 的 `dsh-agent-presets` + `dsh-persona`）已收敛出清晰模型：**智能体是会话级身份，skill 是消息级内容，二者正交组合**。本 change 按此模型补上两条缺失的通道，并顺手把 SKILL.md 契约对齐主流。

## What Changes

- **新增智能体预设（agent preset）**：磁盘声明式智能体定义（`agents/<name>.md`：frontmatter 人设元数据 + 正文 system prompt），加载为注册表；内容来源参考 `agency-agents`。
- **新建对话时选择智能体**：新对话页在知识库选择器**右侧**新增智能体选择器（默认项文案「默认」）；**选定后本次会话不可更改，只能新建对话**（会话级不可变）。
- **智能体作为会话主 agent**：选定 agent preset 后，其正文作为本会话主 agent 的 system prompt（注入点 `src/rag/prompt.py`），会话内持续生效。
- **`/xxx` 显式调用 skill**（消息级触发）：用户可在任意一条消息用 `/name` 前缀加载 skill，**不受智能体选择影响**（skill 与 agent 解耦）；输入区另提供**技能选择器**（深度思考旁下拉）作为按钮式入口，选中即插入 `/name `，与 `/` 补全共用同一候选与同一后端解析。
- **skill 加载后持续生效**：`/xxx` 触发时 skill 内容进入会话上下文（注入一条隐藏消息），**后续轮次仍受影响**，直到上下文压缩或新会话——即"触发粒度=消息级，生效范围=会话级"。
- **fork 执行者归属**：`context: fork` 的 skill 默认由**本会话选定的 agent preset** 执行；skill 可用 `agent:` 显式覆盖（默认系统默认 prompt）。同时 **fork 子代理放开 `allowed-tools`**（修订 `agent-delegation-skills` D7），配套独立 RequestContext。
- **执行框架对齐主流**：子代理改用 `langchain.agents.create_agent`（**agent-as-tool** 委托，脱离已废弃的 `langgraph.prebuilt.create_react_agent`）；middleware **保留装配位但 v1 传空**（引用/校验在 middleware 能力外，唯一收益 `SummarizationMiddleware` 待实测触发条件再开）。
- **system prompt 三层组装**：① 人设层（未选 agent = 现有 prompt；选了 = preset 正文）+ ② 环境约束层（KB 检索纪律 / 引用要求，**系统强制叠加，不可被 preset 覆盖**）+ ③ 运行时层（inline skill / verify 指引）。
- **子代理不直接交互 + 确认门**：子代理不持有 `ask_user`；需要确认时由**编排层确认门**（规则判断）走 ask_user，用户答后**重跑子代理**（上限 1 次）。
- **能力清单接口**：`skills/catalog.json` → `GET /api/skills`、`agents/catalog.json` → `GET /api/agents`（供前端选择器与 `/` 补全），附**启动一致性护栏**。
- **双轴调用控制**：新增 `user-invocable` / `disable-model-invocation`，默认由 `allowed-tools` ⊕ 工具 `readonly` 推导（fail-safe）。
- **delegate 并发安全**：修复现有隐性缺陷——一轮内多个 `delegate_task` 会被 ToolNode 并发调度（`asyncio.gather`），而委派状态写在 `RequestContext` 单值字段上会互相覆盖；改为按 `delegate_id` 分槽隔离。
- **SKILL.md 契约对齐主流**：删除私有的 `thinking` 与 `max-iterations`（迭代上限移到 agent 定义的 `maxTurns`）；`allowed-tools` 改逗号分隔字符串；参数占位符 `{task}` → `$ARGUMENTS`；`context: inline|fork` 保持不变（已与 claude-code 一致）。
- **BREAKING**：`thinking` / `max-iterations` 字段移除；fork 子代理由「恒零工具」改为按 `allowed-tools` 携带工具；`skills/finance-analyst` 由 fork skill 迁移为 agent preset。

## Capabilities

### New Capabilities

- `agent-preset`: 智能体预设的内容契约（`agents/<name>.md` 字段）、加载与注册表、新建对话时的会话级选择与不可变规则、作为会话主 agent 人设注入
- `skill-invocation`: `/xxx` 显式调用 skill（消息级触发、后置持续生效、与 agent 解耦）、双轴调用控制（`user-invocable` / `disable-model-invocation` 及默认推导）、两个通道的候选过滤
- `prompt-composition`: 系统提示三层组装（人设层可替换 / 环境约束层系统强制叠加 / 运行时层），保证未选智能体时默认行为不变
- `capability-catalog`: 能力清单文件（`skills/catalog.json` / `agents/catalog.json`）与两个只读接口（`GET /api/skills` / `GET /api/agents`）+ 启动一致性护栏

### Modified Capabilities

> `skill-registry` / `delegate-task` 目前定义于已实施未归档的 `agent-delegation-skills` change，本 change 修订其部分 Requirement。

- `skill-registry`: frontmatter 契约变更——删 `thinking`、增 `user-invocable` / `disable-model-invocation`；加载期推导双轴默认 + 护栏校验
- `delegate-task`: fork 子代理由"恒零工具"改为"按 `allowed-tools` 携带工具"，需独立 RequestContext；fork 执行者由会话 agent preset 决定（skill 可 `agent:` 覆盖）；fork 的 prompt 结构改为「system=执行者人设、user=skill 内容」；新增**一轮多委派并发安全**（状态按 `delegate_id` 分槽）
- `tool-registry`: `ToolEntry` 新增 `readonly` 属性（双轴推导的事实来源）
- `agent-service`: 流式请求契约新增 `agent`（会话智能体）；会话装配按 agent preset 注入人设
- `chat-harness-ui`: 新建对话页在知识库选择器右侧新增智能体选择器（默认项「默认」）；输入区在深度思考旁新增技能选择器（下拉，选中插入 `/name `）；智能体选定后会话内禁用更改；两个入口的落位与交互见 design D21，设计产物见 `docs/design/pages/chat-agent-skill-selector-2026-09-11.md` + mockup

## Impact

- **新增内容目录**：`agents/`（智能体预设内容库，业务侧管理，compose volume 挂载）；`agents/catalog.json` + `skills/catalog.json`（能力清单）
- **新增代码**：`src/agents/presets/`（AgentPreset 模型 / 加载器 / 注册表）；`src/agents/skills/prefix.py`（`/xxx` 前缀解析）；`src/services/capability_service.py`（清单读取 + 护栏）；`src/api/capabilities.py`（两个只读接口）；确认门节点
- **修改代码**：`src/agents/skills/`（models/loader/registry/executor/delegate_task——删 `thinking`/`max-iterations`、双轴、fork 改 `create_agent` + 执行者与工具 + `delegate_id` 分槽）、`src/agents/tools/registry.py`（ToolEntry.readonly）、`src/rag/prompt.py`（**三层组装**）、`src/config/prompts.py`（环境约束层常量拆分）、`src/services/agent_service.py`（装配 presets + `agent` 校验/持久化 + 前缀路由）、`src/api/model/request.py` + `src/api/chat.py`（`agent` 字段）、`src/chat/manager.py`（会话存 `agent`）、`src/infra/llm/request_context.py`（`delegate_id` 分槽）
- **前端**：`deploy/nginx/html/chat.html`（新建对话页智能体选择器 = 知识库右侧 + 技能选择器 = 深度思考右侧 + `/` 命令补全 + 请求体 `agent`）；实现走 `frontend-design` skill，按 `docs/design/pages/chat-agent-skill-selector-2026-09-11.md` 规格与 `chat-agent-skill-selector-mockup-2026-09-11.html` 落地，改完用 `playwright-cli` 验证并同步 `MASTER.md`
- **契约/文档**：`docs/agents/api_contract.md`（`agent` 字段与 `/xxx` 语义）、`docs/agents/glossary.md`（agent preset / 会话级 vs 消息级）、`docs/agents/code-map.md`（`agents/` 目录）、`docs/agents/reference-projects.md`（agency-agents 作为预设来源）
- **Spec 修订**：`agent-delegation-skills` D7（零工具）、`delegate-hardening-observability`「fork thinking 跟随请求级 deep_thinking」
- **测试**：`tests/agents/skills/`（删 thinking 用例、增双轴/工具隔离/前缀路由）、新增 `tests/agents/presets/`
- **部署**：`agents/` 需 volume 挂载（同 `skills/`）；两份 `catalog.json` 随各自目录挂载；**无新增依赖**（`langchain.agents.create_agent` 在已装 `langchain==1.3.11` 中即存在，本 change 不升级依赖）
