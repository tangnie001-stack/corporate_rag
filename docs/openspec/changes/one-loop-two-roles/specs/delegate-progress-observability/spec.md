# delegate-progress-observability Specification (Delta)

## MODIFIED Requirements

### Requirement: 共享事件转换管道带 scope

系统 SHALL 将 LangGraph 事件→SSE/日志的转换收敛为带 `scope`（`main` 或 `delegate`）的共享转换器；主 agent 与 fork 子代理事件走同一转换逻辑，仅 scope 不同。子代理事件 SHALL NOT 依据主循环的节点判别键被当作主答案处理。

主循环统一到 `create_agent` 之后，主循环的模型事件其节点判别键为 `metadata.langgraph_node == "model"`（`create_agent` 的模型节点名），**不再是 `"agent"`**；工具事件仍为 `"tools"`。故"不得被当作主答案处理"的判据 SHALL 对齐到 `"model"`。

**判据当前实际只有「节点判别键」一维**：转换器的 `scope` 形参虽存在且带 `scope != "main"` 早退，但**所有调用点都用缺省 `main`**（其 docstring 自陈"当前实现下 graph 事件仅在 `scope=="main"` 时转换"）⇒ SHALL NOT 把 `scope` 当作已生效的第二道防线来论证。

**主 SSE 与子代理事件的隔离由事件路由承担**（`var_child_runnable_config.set(None)` 切断子代理的回调继承 + 委派事件经显式喂事件走委派域），本要求 SHALL NOT 依赖转换器侧的第二层匹配。由于判据由 `"agent"` 改为 `"model"` 后，子代理的模型事件（其节点名**同为 `"model"`**）不再被节点名偶然挡住，系统 SHALL 保留一条「**fork 事件不泄漏进主 SSE**」的自动化断言，作为该单点机制的守护。

SHALL NOT 引入 `checkpoint_ns` 前缀等更宽的匹配条件——该值含 uuid 后缀、且只承载外层节点名，多一个匹配面会在来源命名空间变化时把非主循环事件误吸进主 SSE。

#### Scenario: main 与 delegate 同管道分流
- **WHEN** 主 agent 或 fork 子代理产生 LangGraph 事件
- **THEN** 共享转换器按事件归属 scope 分别映射为 main 或 delegate 语义事件

#### Scenario: fork 经显式配置接入
- **WHEN** executor 执行 fork 子代理
- **THEN** 通过显式 callbacks/tags（`delegate`）接入共享管道，且保持与主图的事件隔离（`var_child_runnable_config` 不向外泄漏）

#### Scenario: 主循环模型事件按新判别键识别
- **WHEN** 主循环（`create_agent` 子图）产生 `on_chat_model_start` / `on_chat_model_stream` / `on_chat_model_end`
- **THEN** 转换器 SHALL 依据 `langgraph_node == "model"` 产出主 agent 的 `status` / `token` 事件与 `model_used` 捕获

#### Scenario: 判据不含更宽的匹配面
- **WHEN** 审查转换器的模型事件过滤条件
- **THEN** 条件 SHALL 只由节点判别键（`langgraph_node == "model"`）组成，SHALL NOT 依赖 `checkpoint_ns` 或事件顺序

#### Scenario: 子代理事件不泄漏进主 SSE

- **WHEN** 一次生成内发生 fork 委派（含 `/xxx` 直出轮）
- **THEN** 子代理的模型增量 SHALL NOT 出现在主答案的 token 流与完整答案累积中
- **AND** 该性质 SHALL 由一条自动化断言守护（隔离依赖事件路由，属单点机制，见本条正文）
