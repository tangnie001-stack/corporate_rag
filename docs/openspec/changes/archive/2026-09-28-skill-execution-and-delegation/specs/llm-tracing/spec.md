# llm-tracing Specification (Delta)

## ADDED Requirements

### Requirement: 子代理的工具调用记为 trace span（委派域）

fork 子代理运行期间的工具调用 SHALL 记为同一条 trace 上的 span，挂在**委派父 span** 之下。委派父 span SHALL 标注 `delegate_id` 与该次委派的 `skill`（通用委派标注为通用）。

span 采集 SHALL **复用主图工具 span 的同一采集实现**，按事件归属标识（`scope`：`main` = 主图 / `delegate` = fork 子代理，见 `delegate-progress-observability`「共享事件转换管道带 scope」）区分域；SHALL NOT 为委派路径另立一份同构实现。

采集 SHALL 由消费子代理事件流的一方**显式喂事件**驱动，SHALL NOT 依赖恢复 LangChain 配置继承（`var_child_runnable_config` 的隔离必须保持，理由是它同时防 SSE token 污染与 `full_answer` 累积子代理原文）。

观测失败 SHALL NOT 影响对话：任何 span 建/关异常只记 warning。所有 span SHALL NOT 悬空——采集器的 `close()` 必须挂 `finally`。

#### Scenario: 子代理工具调用出现在 trace

- **WHEN** `/xxx` 直出或主 agent 委派触发的子代理调用了检索工具
- **THEN** 该 trace 上出现一条工具 span，其名字可区分归属（委派域带 `delegate:` 前缀）
- **AND** 该 span 的 `input` 为该工具调用的干净实参、`output` 为返回值（`ToolMessage` 拆成 `{tool_call_id, name, content}`）

#### Scenario: 委派父 span 承载标识

- **WHEN** 一次委派开始执行
- **THEN** 该 trace 上出现一条委派父 span，标注 `delegate_id` 与 `skill`
- **AND** 该次委派内的工具 span 均挂在其下
- **AND** 委派结束时父 span 同步关闭（含 idle / total / turn / cancelled 等中断路径）

#### Scenario: 域之间不串台

- **WHEN** 同一请求内主图与子代理的事件交错到来
- **THEN** 两者的 span 分属各自的父 span，互不挂错
- **AND** 主图工具 span 的既有结构不变

#### Scenario: SSE 隔离不受影响

- **WHEN** 子代理事件被喂给采集器
- **THEN** 这些事件 SHALL NOT 进入外层 `graph.astream_events` 的 SSE 转换
- **AND** `var_child_runnable_config` 的隔离保持既有行为

#### Scenario: 观测故障不阻断对话

- **WHEN** Langfuse 客户端建 span 或关 span 抛异常
- **THEN** 只记 warning，本轮生成照常继续
- **AND** 取消/异常路径下不遗留未关闭的 span
