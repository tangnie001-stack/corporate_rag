# answer-verification Specification (Delta)

> **SUPERSEDED（2026-09-07）**：本 Delta 中"忠实度校验 / 态 B 跑 judge"相关内容已随
> 在线 judge 移除作废（质量评估转离线另行规划）；其余 Requirements 不变。

## MODIFIED Requirements

### Requirement: 答案校验节点

校验节点内部结构 SHALL 采用**两态校验器管道**：会话未绑定 KB（纯对话）与绑定 KB（RAG）各有一套校验管道，`verify_node` SHALL 按会话 kb 状态分派，任一校验器返回决策（重生成/直通）即短路。

#### Scenario: 纯对话分派

- **WHEN** 会话未绑定知识库（`kb_id` 为空）
- **THEN** verify 执行纯对话管道（联网引用引导），不做年份完整性/忠实度校验

#### Scenario: 绑定 KB 分派

- **WHEN** 会话绑定知识库（`kb_id` 非空）
- **THEN** verify 执行 KB 管道（完整性 → KB 溯源护栏），不执行纯对话管道

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

## ADDED Requirements

### Requirement: KB 答案强制溯源

会话绑定 KB 且本轮检索到 KB 上下文时，系统 SHALL 校验最终答案是否带 `[n]` 引用标记；若调用了 retrieve_kb 并产生 kind=kb 上下文、但答案不含任何 `[n]` 且非拒答、且未表达"知识库未覆盖"，则 SHALL 注入引用标注指引 SystemMessage 并触发重生成，防止前端来源缺失。

#### Scenario: KB 答案未带引用被引导补标

- **WHEN** 会话绑定 KB，retrieve_kb 返回 kind=kb 上下文，最终答案无 `[n]` 标记且非拒答
- **THEN** verify 注入"请为引用标注来源编号"指引并重生成，重生成答案带 `[n]` 后进入引用格式化

#### Scenario: 检索但认为无关不强灌引用

- **WHEN** 模型调了 retrieve_kb 但判断结果与问题无关，答案表达"知识库未覆盖"或拒答
- **THEN** 不触发强制溯源，答案直接进入引用格式化
