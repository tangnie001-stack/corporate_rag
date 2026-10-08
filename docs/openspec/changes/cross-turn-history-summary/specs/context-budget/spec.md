## MODIFIED Requirements

### Requirement: 历史裁剪结果 SHALL 落在预算内

**注入的跨轮历史总量**——即「**摘要段**（若存在） + 保留的尾部」——SHALL 不超过配置的绝对预算（最近 1 轮完整保留的既有例外除外，且该例外 SHALL 被显式记录）。摘要段 SHALL NOT 因不在裁剪路径上而绕过该口径；超限时系统 SHALL 先缩减摘要，仍超再裁剪尾部。

#### Scenario: 裁剪后不超预算

- **WHEN** 对一段超出预算的历史执行裁剪
- **THEN** 裁剪结果的总 token 量 SHALL ≤ 配置预算
- **AND** 返回新列表，不修改入参

#### Scenario: 摘要段计入预算

- **WHEN** 组装「摘要段 + 保留尾部」
- **THEN** 其聚合 token 量 SHALL ≤ 配置预算
- **AND** 超限时 SHALL 先缩减摘要、仍超再裁剪尾部
- **AND** SHALL NOT 因「摘要段在裁剪之后追加」而跳过该约束

#### Scenario: 最近一轮的保留例外被显式记录

- **WHEN** 即使仅保留最近 1 轮也会超过预算
- **THEN** 系统 SHALL 完整保留最近 1 轮
- **AND** SHALL 记录该例外（含预算与实际用量），SHALL NOT 静默超出
