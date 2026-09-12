## Why

当前主 agent 是单循环（agent ↔ tools → verify → format），所有能力（检索/联网/澄清）以工具形式平铺注入。企业助手未来挂多个业务域（财务/法务/客服等），需要"领域专家能力"——但领域专家的深度分析、独立人格、专用方法论若全塞进主 agent 工具集与 system prompt，会：

1. **主 agent 上下文膨胀**：多域专家人格（如 agency-agents 单篇 13KB）不可能全量注入
2. **能力与代码耦合**：新增领域能力需改代码/改 prompt，无法业务侧自行增删
3. **无法按需委派**：深度任务（建模/多步推理）应在隔离上下文里跑，而非挤占主对话

本 change 引入**主从委派 + skill 加载器**：主 agent 保留为"掌握全局的执行者"，通过 `delegate_task` 工具按需调用 skill；skill 是磁盘上的声明式能力文件（`skills/<name>/SKILL.md`），加载成运行时注册表，业务侧可增删改而无需改代码。这是从 claude-code（委派范式）/ deepseek-harness（配置机制）/ agency-agents（预设内容）三者抽象出的、贴合本 harness 的落地方案。

## What Changes

**新增能力域（主从委派）**：

1. **Skill 文件与加载器**：`skills/<name>/SKILL.md` 声明式能力文件（frontmatter：name/description/context/model/thinking/allowed-tools/max-iterations），启动/懒加载扫描成进程内 SkillRegistry。skill 目录变化触发懒重载，已披露 skill 的内容修改免重启生效（运行期新增 skill 的 LLM 可见性需图重建/重启，见 design Risks「tool description 静态」）。
2. **`delegate_task` 工具**：主 agent 工具集新增 delegate_task(task, skill)。skill 列表经其 description 渐进披露（只列名 + whenToUse）。skill 的 context 决定执行方式：
   - **inline**：skill 指令注入主 agent 上下文，主 agent 自己执行（"很多时候主 agent 答"）
   - **fork**：skill 生成独立子代理（create_react_agent）执行，隔离上下文、无检索工具，结果文本回主 agent（深度分析）
3. **fork 子代理执行器**：create_react_agent + 传入的 llm 实例（skill 可覆盖 model/thinking）。**子代理工具集恒空**（allowed-tools 预留，见 design D7——现有业务工具写共享 ctx 会污染主 agent tool_contexts）。结果截断回主 agent。可观测性与主 agent **同级观测**（同一 llm 实例自带 callbacks；Langfuse 是否捕获取决于网关层，见 design D11）。
4. **SSE 状态**：fork 执行期间推 `STAGE_DELEGATE` 状态（开始/结束，仅 fork 命中，inline 不推），文案进 SSEInteractionTexts。
5. **迭代预算联动**：delegate 后主 agent 获整合余量（+2 轮），单请求总上限仍封顶防死循环；verify regen 轮复位 _delegate_used 保持"全新 5 轮预算"语义。

**典型工作流**：
- 轻量领域问题 → 主 agent 命中 inline skill → 注入方法后自己答
- 深度分析 → 主 agent delegate fork skill（如财务建模专家）→ 子代理独立分析（材料由主 agent 先检索好塞 task）→ 结果文本回主 agent → 主 agent 整合、verify/format 兜底最终引用

## Capabilities

### New Capabilities

- `skill-registry`: skill 文件扫描、frontmatter 解析、运行时注册表、懒重载、防腐校验
- `delegate-task`: delegate_task 工具（inline/fork 双执行路径）、fork 子代理执行器、结果截断回流
- `delegate-observability`: STAGE_DELEGATE SSE 状态、子代理同级观测（llm 实例 callbacks）、迭代预算联动与 regen 复位、委派装配告警（DELEGATE_SKIP）

### Modified Capabilities

- `tool-registry`: 工具注册表新增 delegate_task 工具（spec-level 行为变化：主 agent 工具集不再是固定三件套）

## Impact

- **新增目录**：`skills/`（skill 内容库，业务侧管理）、`src/agents/skills/`（加载器/注册表/执行器实现）
- **新增文件**：SkillRecord/SkillRegistry/SkillLoader/SkillExecutor/delegate_task/SSE 常量；Event 枚举增 DELEGATE_SKIP
- **修改**：`src/agents/tools/rag_tools.py`（make_rag_tools 注册 delegate_task）、`src/config/const.py`（STAGE_DELEGATE + 文案 + EXPERT_ANALYSIS_MARKER）、`src/config/settings.py`（SKILLS_DIR 可选）、`src/agents/graph/state.py`（迭代预算字段 + _delegate_used）、`src/agents/graph/agent_node.py`（agent_model 置位 + route_agent 预算放宽）、`src/agents/graph/verify/guardrails.py` + `regen_decision.py`（regen dict 复位 _delegate_used；kb_citation_guardrail 专家分析豁免）、`src/agents/graph/workflow.py`（build_graph 透传 delegate_task，D20）、`src/services/agent_service.py`（AgentService 装配 + _convert_event status 分支）、`src/core/log_events.py`/`log_event_specs.py`（DELEGATE_SKIP 登记）、`src/cli/check_docs.py`（skill 目录纳入防腐扫描）
- **依赖**：PyYAML 已可用（6.0.3）；create_react_agent 已可用（langgraph 1.2.9，pyproject 锁定）；无新增第三方依赖
- **构建期解耦**：SkillExecutor **不依赖 make_rag_tools 实例**（避免 make_rag_tools → delegate_task → executor → make_rag_tools 循环依赖）。fork 子代理**零工具**（D7），executor 无需构造子代理工具集，循环依赖天然消失
- **装配（D20）**：delegate_task 由 AgentService.__init__ 构造（SkillRegistry + SkillExecutor(self._llm)）经 build_graph 默认分支注入 make_rag_tools；skills 目录缺失/注册表空时记 DELEGATE_SKIP warning 且不注册——防部署 volume 未挂载时静默退回固定工具集
- **部署**：`skills/` 目录需以 volume 挂载进容器（如 `./skills:/app/skills`）——否则改 skill 要 rebuild 镜像，与"热更新免重启"诉求矛盾。docker-compose.prod.yml 补挂载
- **与活跃 change 边界**：新增 STAGE_DELEGATE（不碰 sse-tool-detail 的 detail 结构）；子代理与主 agent 同级观测、不重构 llm-callback-handler、不新增 Langfuse 应用层专项（D11）；verify 改动仅限 delegate 预算接线 + regen 复位 _delegate_used + kb_citation_guardrail 的 EXPERT_ANALYSIS_MARKER 豁免（不动 retrieval-quality-signals 的 verify 判定）
- **首批 skill 内容**：1 个 inline（财务问答规则）+ 1 个 fork（从 agency-agents finance 域裁剪适配）
