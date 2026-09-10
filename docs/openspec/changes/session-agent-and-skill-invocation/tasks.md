## 1. 契约层（SKILL.md frontmatter）

- [ ] 1.1 `SkillRecord` 删除 `thinking` 与 `max_iterations` 字段、新增 `user_invocable` / `disable_model_invocation`（`src/agents/skills/models.py`）
- [ ] 1.2 `SkillLoader` 解析：移除 `thinking` / `max-iterations`（忽略并记 warning）、新增双轴字段、`allowed-tools` 支持**逗号分隔字符串**（`src/agents/skills/loader.py`）
- [ ] 1.3 更新存量 skill frontmatter（如 `skills/finance-qa` 冗余的 `context: inline`），确认无 `thinking` 使用
- [ ] 1.4 更新/删除 `thinking` 相关测试用例（`tests/agents/skills/test_skill_loader.py`、`test_skill_models.py`、`test_skill_executor.py`）

## 2. 工具读写属性

- [ ] 2.1 `ToolEntry` 新增 `readonly: bool = True`（`src/agents/tools/registry.py`）
- [ ] 2.2 各工具注册点声明 `readonly`（现有 retrieve_kb / search_web / ask_user 均为 True）
- [ ] 2.3 补充测试：默认值与显式声明

## 3. 智能体预设层

- [ ] 3.1 新增 `AgentPreset` 数据模型（name / description / system_prompt / tools / skills / **max_turns** / source_path）—— **v1 不含 `model`**（`src/agents/presets/models.py`）
- [ ] 3.2 新增 `AgentPresetLoader`：解析 `agents/<name>.md`（frontmatter 用**驼峰**键如 `maxTurns` + 正文 system prompt，fail-open）
- [ ] 3.3 新增 `AgentPresetRegistry`：按名索引 + 懒重载 + 同名 fail-fast
- [ ] 3.4 `AgentService` 装配 presets（`agents/` 缺失/空时记 warning 并降级默认）
- [ ] 3.5 内容迁移：`skills/finance-analyst` 的**人设段** → `agents/finance-expert.md`（参考 `agency-agents` finance 域，仅取身份/使命/规则，中文化裁剪）；原文件**改写为方法论** skill（去掉"你是一名…"）
- [ ] 3.6 新增测试 `tests/agents/presets/`：加载 / 注册 / 冲突 / 缺省继承
- [ ] 3.7 新增 `agents/catalog.json`（智能体清单：name/display_name/description）与 `skills/catalog.json`（skill 清单：name/description）

## 4. fork 执行放开工具（修订 D7）

- [ ] 4.1 为 fork 子代理建立**独立 RequestContext**（隔离 `tool_contexts` / 引用编号 / `pending_asks`），并把委派状态从 ctx 单值改为**按 `delegate_id` 分槽**（修 `request_context.py:58` 单值并发缺陷）
- [ ] 4.2 `SkillExecutor` 改用 `langchain.agents.create_agent`（替代废弃的 `create_react_agent`）：system prompt = 执行者人设（agent preset 正文 / 系统默认 prompt）、user message = skill 内容、tools = 执行者 tools ∩ allowed-tools（空则零工具）；`middleware` 参数**保留装配位但传空**
- [ ] 4.3 执行者选择顺序：skill `agent:` > 会话选定智能体 > **系统默认 prompt**（`PromptManager.get_system_prompt()`，非新造 general-purpose）
- [ ] 4.4 简化 `_resolve_fork_llm`：移除 `record.thinking` 优先级与 `no_main_model_name_thinking_fallback`，思考跟随 `ctx.deep_thinking`；子代理最大轮次改取执行者 `maxTurns`（替代 skill `max_iterations`）
- [ ] 4.5 打破循环依赖（工具工厂注入 / 延迟构造，勿让 executor 直接持有 `make_rag_tools`）
- [ ] 4.6 新增**确认门**节点：规则检测子代理返回的"需确认"信号 → 走 `ask_user`（复用澄清链路）→ 答复后重跑子代理（上限 1 次）；无信号直通 verify；被拒/超时/槽被占 → 出结论 + 标注"未经确认"
- [ ] 4.7 测试：工具隔离不污染主 agent、执行者选择优先级、thinking 移除后行为、**一轮多委派并发不串号**、确认门四个分支、用的是 `create_agent` 而非 `create_react_agent`

## 5. 调用控制与 `/xxx` 路由

- [ ] 5.1 双轴默认推导：按 `allowed-tools` ⊕ 工具 `readonly` 计算（fail-safe），加载期护栏 warn（含写类未显式声明 / 死 skill）
- [ ] 5.2 注册表提供 `user_visible()` / `model_visible()` 两个候选列表
- [ ] 5.3 `delegate_task` 按 `model_visible()` 过滤工具 description 可用列表
- [ ] 5.4 `/xxx` 前缀解析（`agent_service` 生成入口）：命中则注入隐藏消息 + 按 context 执行，跳过模型判断；`/` 后剩余文本按 `$ARGUMENTS` 注入 skill 正文（无占位符时追加 `ARGUMENTS: <输入>`）
- [ ] 5.5 未命中 skill → 返回"不存在 + 可用列表"；非 `/` 前缀 → 按普通文本
- [ ] 5.6 请求契约新增 `agent` 字段（`src/api/model/request.py`、`src/api/chat.py`）
- [ ] 5.7 会话智能体**持久化 + 不可变校验**：会话状态存 `agent`（`src/chat/manager.py`）；请求 `agent` 与会话已有值不同 → 拒绝 400；未知 agent → 降级默认 + warn
- [ ] 5.8 **system prompt 三层组装**：`build_system_prompt(persona, kb_bound, has_skills)`（`src/rag/prompt.py`）——①人设层（selected preset 正文 / `PromptManager.get_system_prompt()`）+ ②环境约束层（`config/prompts.py` 拆出检索纪律/引用要求/禁止检索常量）；`INLINE_CITATION_INSTRUCTION` 移入环境约束层；**保证未选 agent 时输出逐字不变**
- [ ] 5.9 **能力清单服务 + 接口**：`src/services/capability_service.py`（读 catalog + mtime 缓存 + 失败降级空列表）；`src/api/capabilities.py`（`GET /api/skills` / `GET /api/agents`，经 service 不直接读文件）
- [ ] 5.10 **一致性护栏**：启动时对比 catalog ↔ 目录，不一致记 warning（"清单有但目录无"/"目录有但未登记"）
- [ ] 5.11 测试：前缀路由 / 注入后持续生效 / 双轴过滤 / agent 降级 / inline 与 fork 两条路径 / **未选 agent 时 system prompt 快照逐字一致** / 两接口结构与空列表降级 / 护栏 warn

## 6. 前端

> **执行方式（本节所有任务适用）**：前端改动（`deploy/nginx/html/chat.html`）统一**调用 `frontend-design` skill 落地**——视觉方向、排版、交互动效按 `docs/design/pages/chat-agent-skill-selector-2026-09-11.md` 规格与 `docs/design/chat-agent-skill-selector-mockup-2026-09-11.html` 预览实现（落位与交互依据见 design D21）；改完用 `playwright-cli` 对照设计稿验证。

- [ ] 6.1 新建对话页智能体选择器：**知识库选择器右侧**同一行，与 `kb-trigger` 同构胶囊（`user-round` 图标 + 当前名 + chevron），默认项文案「**默认**」，下拉单选（人像图标 + 名称 + 描述 + 圆形单选点 + 底部锁提示「选定后本会话内不可更改」），数据源 = `GET /api/agents`（`deploy/nginx/html/chat.html`）
- [ ] 6.2 技能选择器：**深度思考 chip 右侧**同构 chip（`book-open` 图标 + 文案「技能」+ chevron），菜单**向上弹出**，选中后向输入框行首插入 `/name ` 并置 chip 选中态（显示技能名），菜单首项「不使用技能」，只列 `user-invocable ≠ false`；新对话页与历史对话页均提供，数据源 = `GET /api/skills`
- [ ] 6.3 输入框 `/` 命令补全（行首触发、按名过滤、插入 `/name ` 字面量、隐藏 `user-invocable:false`），与 6.2 共用候选数据与后端解析，数据源 = `GET /api/skills`
- [ ] 6.4 会话内禁用更换智能体 + 历史对话页顶栏只读徽标回显智能体名（历史页不渲染智能体选择器）
- [ ] 6.5 流式请求体携带 `agent`
- [ ] 6.6 两个选择器的无障碍与互斥：`role="button"` + `aria-haspopup="listbox"` + `aria-expanded`；键盘（Enter/Space 开合、↑/↓ 移动、Enter 选中、Esc 关闭并归还焦点）；焦点环可见；与引用抽屉 / 任务面板互斥单开
- [ ] 6.7 落地后按 `docs/agents/ui-design-flow.md` 同步 `docs/design/MASTER.md` 与 `pages/chat-agent-skill-selector-2026-09-11.md`（保证设计与实现一致），用 `playwright-cli` 验证

## 7. 契约与文档同步

- [ ] 7.1 `docs/agents/api_contract.md`：新增 `agent` 请求字段语义、`/xxx` 前缀语义与踩坑
- [ ] 7.2 `docs/agents/glossary.md`：新增「智能体预设」「会话级 vs 消息级」术语
- [ ] 7.3 `docs/agents/code-map.md`：登记 `agents/` 内容目录与 `src/agents/presets/`
- [ ] 7.4 `CLAUDE.md`：目录结构速览补 `agents/`；文档组织表按需登记
- [ ] 7.5 修订 `agent-delegation-skills` 的 D7 说明（标注被本 change 修订）
- [ ] 7.6 `data-flow.md`：补「答案校验与引用格式化链路」一节（**本 change 筹备期已完成**，落地时核对与最终实现一致）
- [ ] 7.7 `reference-projects.md`：更新 agency-agents 条目（作为智能体预设来源）+ 记录本次调研结论（R1 执行框架、`create_react_agent` 废弃）

## 8. 验证与收尾

- [ ] 8.1 `pytest tests/ -v` 全绿
- [ ] 8.2 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 8.3 `python -m src.cli.check_docs` 0 error
- [ ] 8.4 手工 E2E：新建对话选智能体 → **用技能选择器选技能** → 发消息 → 第二轮仍受 skill 影响 → 会话内无法换智能体
- [ ] 8.5 手工 E2E：一句话触发多个可并行委派 → 观察并发执行且事件/看板按 `delegate_id` 不串号
- [ ] 8.6 `docker-compose.prod.yml` 补 `agents/` volume 挂载（同 `skills/`）
- [ ] 8.7 清理死代码：`src/api/chat.py` 的 `get_query_biased_snippet` / `_build_highlighted_snippet` 及随之不再需要的 `jieba` import（两者无任何调用方；归档计划 `2026-07-23-rag-orchestration-phase2` 曾记录应删除但未删）
- [ ] 8.8 手工 E2E：会话**绑定 KB 且选定智能体** → 答案仍带 `[n]` 引用（验证环境约束层未被 preset 覆盖）
- [ ] 8.9 手工 E2E：子代理中途请求确认 → 弹澄清卡 → 答复 → **继续完成**（不从头上重来）
