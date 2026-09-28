# answer-verification Specification (Delta)

## MODIFIED Requirements

### Requirement: 修订终止条件

修订终止 SHALL 由**决策化判定**主导，`_verify_regenerations` 独立计数（上限 `MAX_VERIFY_REGENERATIONS`，默认 2）仅作兜底保险丝。verify 决定是否重生成 SHALL 依据 agent 上一轮实际动作：agent 尚未调用 search_web（或确认联网后首次重生成）→ 注入指引重生成；已调用但 queries 未带全缺失年份 → 重生成并强调一次带全；已调用且 queries 带全缺失年份但答案仍缺 → 判定联网无法补充，标注缺失直通，不再重生成。

重生成 SHALL NOT 受主循环回合上限的影响：重生成轮的主循环预算 SHALL 独立起算，使该轮的 `search_web` 工具调用能完整执行并产出答案。

#### Scenario: 重生成不再受主循环迭代上限吞没

- **WHEN** 首轮 agent 已消耗 4 次主循环迭代，verify 确认联网后触发重生成
- **THEN** 重生成轮的主循环预算独立起算，`search_web` 工具调用完整执行并产出答案

#### Scenario: 联网已尝试且带全缺失年份仍缺则终止

- **WHEN** agent 已调用 search_web 且 queries 覆盖全部缺失年份，但答案仍缺失年份（联网无法补充）
- **THEN** verify 判定不再重试，返回现有答案并标注"知识库与网络均未覆盖"，终止校验-修订循环

#### Scenario: agent 尚未联网则引导重试

- **WHEN** 确认联网后 agent 尚未调用 search_web（或 queries 未带全缺失年份）
- **THEN** verify 注入/重申联网指引并重生成，指引强调一次带全所有缺失年份

#### Scenario: 保险丝预算耗尽

- **WHEN** 重生成轮次达到 `MAX_VERIFY_REGENERATIONS`（兜底保险丝，正常应被决策判定提前终止）
- **THEN** 系统不再重生成，返回现有答案并标注缺失（软拒答），终止校验-修订循环
