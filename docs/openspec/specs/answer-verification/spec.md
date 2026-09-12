# answer-verification Specification

## Purpose
TBD - created by archiving change agent-harness-foundation. Update Purpose after archive.
## Requirements
### Requirement: 答案校验节点

校验节点内部结构 SHALL 采用**两态校验器管道**：会话未绑定 KB（纯对话）与绑定 KB（RAG）各有一套校验管道，`verify_node` SHALL 按会话 kb 状态分派，任一校验器返回决策（重生成/直通）即短路。

#### Scenario: 纯对话分派

- **WHEN** 会话未绑定知识库（`kb_id` 为空）
- **THEN** verify 执行纯对话管道（联网引用引导），不做年份完整性/忠实度校验

#### Scenario: 绑定 KB 分派

- **WHEN** 会话绑定知识库（`kb_id` 非空）
- **THEN** verify 执行 KB 管道（完整性 → KB 溯源护栏），不执行纯对话管道

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

### Requirement: 纯对话轻量自检

会话未绑定知识库（纯对话）时，系统 SHALL 跳过 verify 节点，改用 prompt 行为准则实现轻量自我校验（claude-code 模式，零额外 LLM 调用）：system prompt SHALL 要求模型"不确定/无法验证的内容如实说明、不编造；可联网核实就联网；不把没查证的当查证了"。

#### Scenario: 纯对话不跑 verify

- **WHEN** 会话未绑定知识库，agent 纯对话生成答案
- **THEN** verify 节点不执行，答案直接进入引用格式化；system prompt 的忠实报告准则约束模型不编造

### Requirement: 修订终止条件

修订终止 SHALL 由**决策化判定**主导，`_verify_regenerations` 独立计数（上限 `MAX_VERIFY_REGENERATIONS`，默认 2）仅作兜底保险丝。verify 决定是否重生成 SHALL 依据 agent 上一轮实际动作：agent 尚未调用 search_web（或确认联网后首次重生成）→ 注入指引重生成；已调用但 queries 未带全缺失年份 → 重生成并强调一次带全；已调用且 queries 带全缺失年份但答案仍缺 → 判定联网无法补充，标注缺失直通，不再重生成。`_agent_iterations` 上限 SHALL 只管 agent→tools 主循环，不再被 verify 复用。

#### Scenario: 重生成不再受主循环迭代上限吞没

- **WHEN** 首轮 agent 已消耗 4 次主循环迭代，verify 确认联网后触发重生成
- **THEN** 重生成不受 `_agent_iterations` 上限影响，search_web 工具调用完整执行并产出答案

#### Scenario: 联网已尝试且带全缺失年份仍缺则终止

- **WHEN** agent 已调用 search_web 且 queries 覆盖全部缺失年份，但答案仍缺失年份（联网无法补充）
- **THEN** verify 判定不再重试，返回现有答案并标注"知识库与网络均未覆盖"，终止校验-修订循环

#### Scenario: agent 尚未联网则引导重试

- **WHEN** 确认联网后 agent 尚未调用 search_web（或 queries 未带全缺失年份）
- **THEN** verify 注入/重申联网指引并重生成，指引强调一次带全所有缺失年份

#### Scenario: 保险丝预算耗尽

- **WHEN** 重生成轮次达到 `MAX_VERIFY_REGENERATIONS`（兜底保险丝，正常应被决策判定提前终止）
- **THEN** 系统不再重生成，返回现有答案并标注缺失（软拒答），终止校验-修订循环

### Requirement: KB 答案强制溯源

会话绑定 KB 且本轮检索到 KB 上下文时，系统 SHALL 校验最终答案是否带 `[n]` 引用标记；若调用了 retrieve_kb 并产生 kind=kb 上下文、但答案不含任何 `[n]` 且非拒答、且未表达"知识库未覆盖"，则 SHALL 注入引用标注指引 SystemMessage 并触发重生成，防止前端来源缺失。

#### Scenario: KB 答案未带引用被引导补标

- **WHEN** 会话绑定 KB，retrieve_kb 返回 kind=kb 上下文，最终答案无 `[n]` 标记且非拒答
- **THEN** verify 注入"请为引用标注来源编号"指引并重生成，重生成答案带 `[n]` 后进入引用格式化

#### Scenario: 检索但认为无关不强灌引用

- **WHEN** 模型调了 retrieve_kb 但判断结果与问题无关，答案表达"知识库未覆盖"或拒答
- **THEN** 不触发强制溯源，答案直接进入引用格式化
