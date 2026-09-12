## 1. Skill 内容模型与加载器（skill-registry 核心）

- [ ] 1.1 新建 `skills/` 顶层目录与 `src/agents/skills/` 包；SkillRecord dataclass（name/description/context/inline_prompt/agent_prompt/model/thinking/allowed_tools/max_iterations/source_path，字段行内注释）
- [ ] 1.2 SkillLoader：扫描 `skills/<name>/SKILL.md`，PyYAML 解析 frontmatter（context 非法值回落 inline + warning；正文按 context 存 inline_prompt 或 agent_prompt）；目录名作 skill 名
- [ ] 1.3 SkillRegistry：聚合 SkillRecord 按名索引；同名冲突加载期报错（fail-fast）；`to_tool_description()` 生成渐进披露描述（名 + whenToUse，预算截断）
- [ ] 1.4 懒重载：记录 `skills/` 目录 mtime，`get_or_reload()` 在变化时重扫（delegate_task 调用前检查）
- [ ] 1.5 单测：frontmatter 解析 / context 值约束 / 名称冲突 / 懒重载触发（tests/agents/skills/test_skill_registry.py）

## 2. delegate_task 工具（delegate-task 核心）

- [ ] 2.1 注册：make_rag_tools 增可选参 `delegate_task`（非空才注册进 ToolRegistry）；`delegate_task(task, skill)` 闭包注入 SkillRegistry + SkillExecutor，description 生成时接 `to_tool_description()`（名 + whenToUse，预算截断）
- [ ] 2.2 inline 路径：命中 context=inline → 返回 inline_prompt（{task} 占位填入）
- [ ] 2.3 fork 路径：命中 context=fork → create_react_agent(llm, tools=[], prompt=agent_prompt)（**零工具**：allowed-tools 字段预留不启用，见 design D7）；fork 开始/完成时经 ctx 事件通道投递 STAGE_DELEGATE 状态（`{"type":"status", stage, phase}`，见 3.2）
- [ ] 2.4 模型覆盖：skill 声明 model → `get_llm(model=record.model)` 新建；未声明 → 复用主 agent llm 实例；thinking 可覆盖（extra_body enable_thinking，见 design D12）
- [ ] 2.5 结果截断：fork 返回超阈值 → 截断 ~1000 字摘要（含总字数提示）回主 agent
- [ ] 2.6 超时兜底：`asyncio.wait_for(..., timeout=DELEGATE_TIMEOUT)`，超时返回错误提示
- [ ] 2.7 未知 skill：返回"skill 不存在" + 可用列表
- [ ] 2.8 单测：inline 返回指引 / fork spawn 子代理（mock create_react_agent，patch 模块级名字）/ 截断 / 超时 / 未知 skill / description 含可用 skill（tests/agents/skills/test_delegate_task.py）
- [ ] 2.9 工具集隔离验证：单测断言 fork 子代理 tools 恒空（不含 delegate_task/retrieve_kb/search_web）；防递归由零工具硬保证（tests/agents/skills/test_delegate_task.py）
- [ ] 2.10 真实图接线（design D20）：build_graph 增 `delegate_task` 可选参并在默认分支透传给 make_rag_tools；AgentService.__init__ 装配（skills 目录存在且注册表非空 → SkillRegistry + SkillExecutor(self._llm) → make_delegate_task）；skills 目录缺失/注册表空时记 DELEGATE_SKIP warning 且不注册；图级接线守卫单测（monkeypatch make_rag_tools 断言 delegate_task 参数送达，tests/agents/graph/test_graph.py）

## 3. SSE 状态与迭代预算（delegate-observability）

- [ ] 3.1 const.py：SSEInteractionTexts 新增 STAGE_DELEGATE + 开始/结束文案
- [ ] 3.2 SSE 消费接线：agent_service 为 delegate_task 的 fork 分支内投递的 `{"type":"status", stage, phase}` 加转换/消费分支（`_drain_clarify_channel` 消费 + `_convert_event` 转 SSEStatusEvent，参考 ask_user 的 clarify_channel 先例；inline 命中不投递，见 design D14）
- [ ] 3.3 AgentState/route_agent：delegate 轮后迭代上限放宽（+2 整合余量），单请求总上限封顶；未 delegate 行为不变（tests/agents/graph/test_agent_node.py）
- [ ] 3.4 verify regen 复位预算：guardrails（web_citation_guard/kb_citation_guardrail）与 regen_decision 的 regen dict 补 `_delegate_used: False`；回归断言 regen dict 复位（tests/agents/graph/test_verify_node.py）
- [ ] 3.5 委派装配告警：Event 枚举与 EVENT_SPECS 登记 `DELEGATE_SKIP`（app 层前缀，warning，reason=skills_dir_missing/registry_empty）；通过 tests/core/test_log_events.py 一致性断言
- [ ] 3.6 单测：SSE 状态映射 / 预算放宽触发与封顶 / inline 不推（tests/services/test_agent_service.py + tests/agents/graph/test_verify_node.py）

## 4. 主 agent 引导与首批 skill 内容

- [ ] 4.1 prompts.py：主 agent system prompt 加"何时 delegate vs 自己答"引导段（含 delegate_task 可用 skill 提示）；引导段须含**陈述区隔规则**：检索事实 → 引 tool_contexts 的 [n]；专家分析/建议 → 观点表述不配 [n]，可标注 `EXPERT_ANALYSIS_MARKER` 原文（const.py，"基于领域经验的分析"，防 fork 观点被硬凑 [n] 幻觉引用；措辞与常量同文由测试锁定，见 design D9）；prompt_manager.get_system_prompt 幂等追加引导段（Langfuse 拉取未含时也生效）
- [ ] 4.2 首批 inline skill：`skills/finance-qa/SKILL.md`（财务问答规则：先检索/标报告期/引用 [n]）
- [ ] 4.3 首批 fork skill：`skills/finance-analyst/SKILL.md`（从 agency-agents finance 域裁剪 Financial Analyst → 精简到几百字作子代理 system_prompt；**正文不出现任何工具名**——子代理零工具无工具可调，见 D7/D9；材料由主 agent 预检索塞 task，见 fork spec「材料由主 agent 预检索」）
- [ ] 4.4 端到端冒烟：真实 query 触发 delegate（inline 命中自己答 / fork 命中子代理分析 + 结果回流 + verify/format 引用正常）；含自动回归断言：**纯分析型 fork 答案（零 [n]、含 EXPERT_ANALYSIS_MARKER 措辞）不被态 B kb_citation_guardrail 误触发补标 regen**（M7，tests/agents/graph/test_verify_node.py）

## 5. 防腐扩展与文档

- [ ] 5.1 check_docs.py（或独立 skill-check）：扫描 skill frontmatter 的 allowed-tools 工具名 vs 代码实际工具注册；排除表控制示例/伪代码误报（**首批 skill 按 D19 不写 allowed-tools，本版扫描空转属预期**——机制先建位，allowed-tools 启用即有对象）；另加正文叙述级防腐测试：skill 正文出现的已知工具名（retrieve_kb/search_web/ask_user/delegate_task）须在代码实际注册集合内（tests/cli/test_doc_consistency.py，防 inline 正文引用已改名工具）
- [ ] 5.2 glossary.md 登记术语：skill / SkillRecord / inline 执行 / fork 执行 / delegate_task（对照 CLAUDE.md 文档组织表）
- [ ] 5.3 CLAUDE.md「代码目录结构」补 `skills/` 与 `src/agents/skills/` 说明
- [ ] 5.4 data-flow.md：链路 2 补 delegate 分支描述（主 agent → delegate_task → inline/fork → 回主 agent）
- [ ] 5.5 api_contract.md：登记 SSE stage=STAGE_DELEGATE（推送时机 fork 开始/结束、载荷 {stage, phase}、inline 不推；与 sse-tool-detail 的边界）——STAGE_DELEGATE 是前端对接的新 SSE stage，属接口契约变化
