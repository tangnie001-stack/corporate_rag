## MODIFIED Requirements

### Requirement: 历史注入窗口

系统 SHALL 为 agent 循环历史注入采用「最近 N 轮 + **绝对 token 预算** + **更旧段摘要**」：保留最近 `HISTORY_MAX_TURNS`（默认 10）轮 user/assistant 文本**原文**；历史总 token 超过 `HISTORY_TOKEN_BUDGET`（`src/config/const.py` 的集中绝对值，初值 16384）时从最旧逐条截断；最近 1 轮完整注入。**若会话级存储中存在更旧段摘要，摘要段 SHALL 作为独立历史消息参与注入**，顺序为「system 段 → 摘要段 → 保留的最近若干轮原文」；摘要不存在或不可用时，注入 SHALL 回退为不含摘要的纯裁剪（本要求其余部分不变）。摘要的**生成**发生在**回合正常完成**的收尾、以异步方式进行（见 `history-summarization` 规格），SHALL NOT 延迟本轮的注入与裁剪。token 计数 SHALL 经统一入口 `src/infra/llm/token_count.py`（分词器近似），SHALL NOT 使用字符数粗估算。裁剪后仅剩最近 1 轮仍超预算时，系统 SHALL 保留该轮并记录 `history budget exceeded` 事件（含预算、实际用量与保留条数），SHALL NOT 静默超出。

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

#### Scenario: 有摘要时更旧段以摘要保留而非丢弃
- **WHEN** 会话级存储中存在更旧段摘要
- **THEN** 注入 SHALL 包含该摘要段
- **AND** 顺序为「system 段 → 摘要段 → 保留的最近若干轮原文」

#### Scenario: 无摘要或摘要不可用时回退纯裁剪
- **WHEN** 会话级存储中不存在摘要，或摘要读取/校验不可用
- **THEN** 系统 SHALL 按不含摘要的纯裁剪路径组装历史
- **AND** SHALL NOT 因此中断本对话或改变本轮可见输出

#### Scenario: 摘要在回合收尾生成，不延迟本轮注入
- **WHEN** 某轮触阈并因此触发了摘要生成
- **THEN** 本轮的历史注入与裁剪 SHALL 不受该生成影响（不等待）
- **AND** 生成的摘要 SHALL 自后续轮次起参与注入
