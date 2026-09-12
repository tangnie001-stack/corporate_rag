# chat-harness-ui Delta（session-process-replay）

## ADDED Requirements

### Requirement: 过程区历史回放

历史会话加载（loadSessionMessages）对含非空 `process` 字段的 assistant 消息 SHALL 按事件数组顺序重建过程容器（状态行/旁白块/委派区），渲染产物 SHALL 与实时观看一致；`process` 为 null 的存量消息 SHALL 仅渲染终稿，不报错。本要求取代 D7「历史重载不重建（仅展示终稿）」决策。

#### Scenario: 刷新后过程原样重现

- **WHEN** 用户刷新页面，加载含 process 数据的会话
- **THEN** 该回答上方 SHALL 按事件顺序重建过程容器，正文气泡仅含正式回答（无旁白粘连）

#### Scenario: 存量消息降级

- **WHEN** 加载 process 为 null 的历史消息
- **THEN** 仅渲染终稿与引用，无过程容器，无 JS 错误

### Requirement: 旁白块渲染

实时与历史路径中，判定为旁白的 content 流（最后一次工具调用之前的轮次 content）SHALL 渲染为过程容器内的旁白块（区别于正文的弱化样式：虚线左边框、斜体）；纯问答（全程无工具调用）SHALL 不产生旁白块。

#### Scenario: 旁白块样式区分

- **WHEN** 过程容器内渲染旁白块
- **THEN** 其样式 SHALL 与正文气泡可明确区分（虚线左边框 + 斜体 + 弱化配色）

### Requirement: 相邻同型合并渲染

过程容器渲染 SHALL 将「相邻且同类型」的元素合并为一个组 div，类型不同即分拆，只看相邻不看全局，整体严格按事件到达顺序；实时路径与历史回放 SHALL 共用同一合并实现。

#### Scenario: 连续状态合并

- **WHEN** 过程元素序列为 status, status, status, think, status
- **THEN** 渲染为 3 个组 div（status×3 合一、think 独立、status 独立）

#### Scenario: 类型交错不合并

- **WHEN** 过程元素序列为 status, preamble, think, status, retrieve
- **THEN** 渲染为 5 个组 div
