# prompt-composition Specification (Delta)

## ADDED Requirements

### Requirement: system 段的施加通道逐字不变

system 段的**组装**结果送入模型请求时 SHALL 逐字不变——包括未绑定 KB 时由 `build_system_prompt` 产出的**第二条** system 消息（未绑定提示）SHALL 仍然作为独立 system 消息下发，SHALL NOT 被合并进第一条、也 SHALL NOT 降级为普通消息。

组装仍由 `build_system_prompt(...)` 承担、**每次生成 SHALL 只执行一次**；本要求只约束**施加通道**（从组装结果到模型请求这一段），不改变三段式边界与追加顺序。

组装结果 SHALL 由**跨轮次持久**的载体承载，使**重生成轮**（校验节点触发、重新进入主循环）仍能取到同一份 system 段、SHALL NOT 在重生成轮因"消息非空而跳过组装"导致 system 段丢失；同时重生成轮 SHALL NOT 触发第二次组装。

#### Scenario: 未绑 KB 时两条 system 消息都到位

- **WHEN** 会话未绑定 KB，本轮组装产出两条 system 消息
- **THEN** 模型请求中 SHALL 依次包含这两条 system 消息，内容与组装结果逐字一致

#### Scenario: 每次生成只组装一次

- **WHEN** 一次生成内主循环发生多轮模型调用
- **THEN** system 段组装 SHALL 只发生一次，其组装事实日志（`prompt assembled` 语义的事件）SHALL 每次生成只产出一次

#### Scenario: 重生成轮仍带完整 system 段

- **WHEN** 校验节点要求重生成，主循环被再次进入
- **THEN** 重生成轮的模型请求 SHALL 仍包含完整的 system 段（含未绑 KB 时的第二条）
- **AND** SHALL NOT 触发第二次 system 段组装

#### Scenario: 组装通道变化不改变快照

- **WHEN** 施加通道改由 middleware 承担
- **THEN** 「默认行为逐字不变（端到端快照）」要求 SHALL 仍然成立（以端到端输出为准）
