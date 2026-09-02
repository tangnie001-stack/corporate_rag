# answer-verification Specification

## Purpose
TBD - created by archiving change agent-harness-foundation. Update Purpose after archive.
## Requirements
### Requirement: 答案校验节点

系统 SHALL 在 agent 生成完成（`agent_finalize`）之后、引用格式化（`format`）之前，执行答案校验节点；校验不通过 SHALL 触发询问或修订，不得直接返回未校验的答案。

#### Scenario: 校验节点挂载

- **WHEN** agent 循环生成最终答案
- **THEN** 答案先经校验节点检查完整性/忠实度，通过才进入引用格式化

### Requirement: 完整性校验

系统 SHALL 以结构化方式比对"问题要求覆盖的年份/实体范围（来自时间约束等前置解析）"与"答案中实际覆盖的年份/实体"；答案覆盖年份 SHALL 用正则提取（`\d{4}`，答案文本来自 `AgentState.answer`）；缺失时**不自动修订补充**，而是触发询问用户是否联网补充（见"缺失年份联网询问"）。

#### Scenario: 答案只覆盖 2024 而要求 2023-2025

- **WHEN** 问题要求覆盖 [2023, 2024, 2025]，答案仅覆盖 2024
- **THEN** 完整性校验判定缺失 [2023, 2025]，触发询问用户是否联网补充（而非自动修订）

#### Scenario: 答案标注全部要求年份

- **WHEN** 答案文本含 2023、2024、2025 年份，要求覆盖 [2023, 2024, 2025]
- **THEN** 完整性校验通过，直接进入引用格式化

### Requirement: 缺失年份联网询问

会话绑定知识库且完整性校验检测到缺失年份时，系统 SHALL 通过 `ask_user` 机制（复用 `clarify_channel` 投递，同现有澄清卡片）询问用户"是否联网补充缺失年份"；**用户确认后才调用 search_web 联网补充**，拒绝则返回现有答案并标注"知识库仅覆盖 X 年"。**会话内记住确认**（`RequestContext.web_confirmed`）：用户确认过"需要联网"后，**单轮请求内**后续缺失年份不再询问，直接联网（跨轮持久化留 P1）。**确认询问独立计数**：不计入 `MAX_ASK_PER_TURN`（新增 `MAX_VERIFY_ASK_PER_TURN=1` 防御上限，每轮最多询问 1 次，避免与 LLM 澄清互相挤占额度）。

**重生成机制（防死循环）**：用户确认后，verify 节点 SHALL 向 `state.messages` 追加一条 `SystemMessage`（含缺失年份与"请调用 search_web 补充"指令），再置 `_needs_regenerate=True` 回 agent 重生成——否则 LLM 用原消息重生成相同答案、不会调 search_web，verify→agent 无限循环。`_ask_web_confirm` 必须为 async 函数（复用 ask_tools 的 `_wait_with_abort_and_timeout` 与 `pending_asks` 单槽保护；槽被 LLM 澄清占用时放弃询问按"未确认"处理；禁止 `run_until_complete`）。

#### Scenario: 用户确认联网

- **WHEN** 完整性校验检测缺失 [2023, 2025]，用户回答"需要联网"
- **THEN** 系统调用 search_web 补充缺失年份（多 query 一次调用），重新生成答案后再次校验

#### Scenario: 用户拒绝联网

- **WHEN** 完整性校验检测缺失 [2023, 2025]，用户回答"不需要"
- **THEN** 系统返回现有答案并标注"知识库仅覆盖 2024 年"

#### Scenario: 会话内已确认不再询问

- **WHEN** 本会话用户已确认过"需要联网"（`web_confirmed=true`），后续又检测到缺失年份
- **THEN** 系统直接调用 search_web 联网补充，不再询问

### Requirement: 忠实度校验

系统 SHALL 检查答案中的事实点是否被检索证据（引用上下文）支撑；无支撑的事实点 SHALL 被标记或触发修订，防止编造。忠实度判断 SHALL 使用独立评估模型（复用 `RAGAS_LLM_MODEL`，temperature 固定 0），与生成模型隔离；judge SHALL 输出无支撑句子清单供修订参考，不直接删除答案内容。**触发时机**：judge SHALL 仅在完整性校验通过后的最终答案运行（中途"询问联网→重生成"的过程答案不跑 judge），一次问答 judge 最多 1-2 次，受 2 轮上限约束。

#### Scenario: 答案包含无支撑断言

- **WHEN** 最终答案某句事实无法从任何引用上下文找到依据
- **THEN** 忠实度校验用 judge 模型标记该句无支撑（`_unsupported` 写入 state/日志）；P0 仅记录不驱动流程，触发 LLM 删除/修正无支撑内容的修订动作留 P1 输出护栏

### Requirement: 纯对话轻量自检

会话未绑定知识库（纯对话）时，系统 SHALL 跳过 verify 节点，改用 prompt 行为准则实现轻量自我校验（claude-code 模式，零额外 LLM 调用）：system prompt SHALL 要求模型"不确定/无法验证的内容如实说明、不编造；可联网核实就联网；不把没查证的当查证了"。

#### Scenario: 纯对话不跑 verify

- **WHEN** 会话未绑定知识库，agent 纯对话生成答案
- **THEN** verify 节点不执行，答案直接进入引用格式化；system prompt 的忠实报告准则约束模型不编造

### Requirement: 修订终止条件

系统 SHALL 为校验-修订循环设置轮次上限：完整性校验（结构化，快）每次生成后执行；LLM judge 最多 2 轮。达到上限仍不达标 SHALL 转拒答（标注信息不足）或转人工，不得无限循环。**P0 实现**：verify 节点自查 `_agent_iterations >= _max_agent_iterations`（=5）时不再置 `_needs_regenerate`，把缺失标注拼到 answer 直通 format（软拒答）——`route_agent` 的上限检查管不到 verify→agent 边，**必须 verify 自查**；"转人工"升级路径留 P1。

#### Scenario: 修订轮次耗尽

- **WHEN** 缺失年份经联网重生成后仍缺失、`_agent_iterations` 达到上限
- **THEN** 系统不再重生成，返回现有答案并标注"知识库仅覆盖 X 年"（软拒答），终止校验-修订循环
