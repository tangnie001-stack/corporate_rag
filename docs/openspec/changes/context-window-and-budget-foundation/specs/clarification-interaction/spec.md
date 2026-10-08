## MODIFIED Requirements

### Requirement: 历史注入窗口

系统 SHALL 为 agent 循环历史注入采用「最近 N 轮 + **绝对 token 预算**」：保留最近 `HISTORY_MAX_TURNS`（默认 10）轮 user/assistant 文本；历史总 token 超过 `HISTORY_TOKEN_BUDGET`（`src/config/const.py` 的集中绝对值，初值 16384）时从最旧逐条截断；最近 1 轮完整注入。token 计数 SHALL 经统一入口 `src/infra/llm/token_count.py`（分词器近似），SHALL NOT 使用字符数粗估算。裁剪后仅剩最近 1 轮仍超预算时，系统 SHALL 保留该轮并记录 `history budget exceeded` 事件（含预算、实际用量与保留条数），SHALL NOT 静默超出。滚动摘要列为后续增强。

#### Scenario: 话题漂移上下文保留
- **WHEN** 用户 A→B→A 话题漂移，A 在最近 N 轮窗口内
- **THEN** A 的上下文保留在注入历史中，回到 A 时模型可继续

#### Scenario: 超预算截断
- **WHEN** 历史轮数超过 `HISTORY_MAX_TURNS` 或总 token 超过 `HISTORY_TOKEN_BUDGET`
- **THEN** 从最旧截断，保留最近 N 轮与最近 1 轮完整内容

#### Scenario: 仅剩最近 1 轮仍超预算
- **WHEN** 裁剪至仅剩最近 1 轮时总 token 仍超过 `HISTORY_TOKEN_BUDGET`
- **THEN** 系统完整保留最近 1 轮
- **AND** 记录 `history budget exceeded`（含 `budget` / `used` / `kept`），不静默超出
