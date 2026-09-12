# delegate-progress-observability Specification

## Purpose
TBD - created by archiving change delegate-hardening-observability. Update Purpose after archive.
## Requirements
### Requirement: 共享事件转换管道带 scope

系统 SHALL 将 LangGraph 事件→SSE/日志的转换收敛为带 `scope`（`main` 或 `delegate`）的共享转换器；主 agent 与 fork 子代理事件走同一转换逻辑，仅 scope 不同。子代理事件 SHALL NOT 依据 `metadata.langgraph_node == "agent"` 被当作主答案处理。

#### Scenario: main 与 delegate 同管道分流
- **WHEN** 主 agent 或 fork 子代理产生 LangGraph 事件
- **THEN** 共享转换器按事件归属 scope 分别映射为 main 或 delegate 语义事件

#### Scenario: fork 经显式配置接入
- **WHEN** executor 执行 fork 子代理
- **THEN** 通过显式 callbacks/tags（`delegate`）接入共享管道，且保持与主图的事件隔离（`var_child_runnable_config` 不向外泄漏）

### Requirement: 子代理过程增量 SSE

系统 SHALL 将 fork 子代理运行中的增量（思考 `reasoning` 与正文 `content`）作为独立 delegate SSE 事件推送前端，事件携带 `kind: thinking|content`、增量文本与 `delegate_id`（及 skill）。一次回答多次委派时，前端按 `delegate_id` 区分，不串流。

#### Scenario: fork 思考流式
- **WHEN** fork 子代理模型产生 reasoning 增量
- **THEN** 推送 delegate 事件（kind=thinking、delegate_id），前端对应过程节实时追加

#### Scenario: fork 正文流式
- **WHEN** fork 子代理模型产生正文增量
- **THEN** 推送 delegate 事件（kind=content、delegate_id），前端对应过程节实时追加

#### Scenario: 多次委派不串流
- **WHEN** 一次回答内发生多次 delegate
- **THEN** 各 delegate_id 的过程节/事件互不混叠

#### Scenario: 增量不进入主答案
- **WHEN** 推送 delegate 增量事件
- **THEN** 不得复用主 token 事件，不得追加到主回答气泡/full_answer

### Requirement: 分析过程折叠区

系统 SHALL 在前端回答内提供"领域专家分析过程"折叠区，仅展示本次委派的子代理过程；过程区默认折叠、可展开，不影响回答正文排版。

#### Scenario: 委派过程展示
- **WHEN** 一次 fork 委派产生过程增量
- **THEN** 渲染在本次回答对应的"领域专家分析过程"折叠区内

#### Scenario: 无委派不展示
- **WHEN** 请求未发生 fork 委派（含 inline skill 命中）
- **THEN** 不渲染过程折叠区

### Requirement: delegate_id 唯一分配与贯穿

系统 SHALL 为每次 `delegate_task` fork 分配唯一 `delegate_id`（如短 uuid），自开始事件起贯穿该次委派的所有事件（start/增量/end）与对应 task 注册表 execution 条目；同一次回答内多次委派 SHALL 使用不同 delegate_id。

#### Scenario: 委派开始时生成
- **WHEN** delegate_task 命中 fork 并开始执行
- **THEN** 生成唯一 delegate_id 并随 start 事件广播

#### Scenario: 同委派事件贯穿
- **WHEN** 同一 fork 产生 start/增量/end/task 事件
- **THEN** 均携带同一 delegate_id

#### Scenario: 多次委派互不相同
- **WHEN** 一次回答内发生多次 delegate
- **THEN** 各次 delegate_id 不同，事件/过程节按 id 隔离

### Requirement: 委派终态文案区分完成与中断

fork 结束状态 SHALL 区分正常完成与中断：正常完成推送"领域专家分析完成"；因 idle/total/turn 中断或失败推送"分析中断（原因）"，不得无条件推"完成"。

#### Scenario: 正常完成
- **WHEN** fork 正常返回结果
- **THEN** 状态文案为"领域专家分析完成"

#### Scenario: 超时/失败中断
- **WHEN** fork 因 idle/total/turn 中断或异常失败
- **THEN** 状态文案含"中断"及原因，不显示"完成"

### Requirement: 子代理轮次日志

系统 SHALL 为 fork 子代理记录可区分于主 agent 的日志事件（`delegate start`/`delegate model turn`/`delegate end`），携带 model、elapsed_ms、usage（input/output tokens）、thinking 是否开启、结果长度；超时场景携带超时原因。日志事件经 Event/EventSpec 注册且随外层 trace 对齐。流式场景 usage 缺失时 SHALL 复用主链 estimate_usage 兜底并标注 usage_estimated。

#### Scenario: 单轮子代理调用
- **WHEN** fork 子代理执行一次模型调用
- **THEN** 记录 delegate model turn 事件（model、usage、latency）

#### Scenario: 子代理整体执行
- **WHEN** fork 子代理开始与结束
- **THEN** 记录 delegate start/end（含 elapsed_ms、结果长度、超时原因如有）

### Requirement: 防污染不变量

系统 SHALL 保证 scope=delegate 的事件永不写入主 `full_answer`、主 token 流或主会话持久化内容；相关防泄漏回归测试保留并覆盖该不变量。

#### Scenario: 子代理文本不污染主答案
- **WHEN** fork 子代理产出文本/推理
- **THEN** 主答案、SSE token 流与落库内容均不包含子代理过程文本（仅最终工具返回值经主 agent 整合）
