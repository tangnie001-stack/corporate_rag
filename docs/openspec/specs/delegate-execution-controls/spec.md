# delegate-execution-controls Specification

## Purpose
TBD - created by archiving change delegate-hardening-observability. Update Purpose after archive.
## Requirements
### Requirement: fork thinking 跟随请求级 deep_thinking

fork 子代理的 `enable_thinking` SHALL 取**请求级 `deep_thinking`**（经请求上下文 `ctx.deep_thinking` 读取）；skill **不持有** `thinking` 声明通道——该 frontmatter 字段已废弃，读到即忽略并记 warning，故不存在 skill 级覆盖。

#### Scenario: 请求关闭深思考

- **WHEN** 当前请求 deep_thinking=false
- **THEN** fork 子代理 enable_thinking=false（不落入模型默认思考）

#### Scenario: 请求开启深思考

- **WHEN** 当前请求 deep_thinking=true
- **THEN** fork 子代理 enable_thinking=true

#### Scenario: skill 声明 thinking 被忽略

- **WHEN** skill frontmatter 声明 `thinking: true`（或 false）
- **THEN** 该声明不生效（加载期忽略并记 warning），`enable_thinking` 仍取请求级 `deep_thinking`

### Requirement: 请求级 deep_thinking 可及

系统 SHALL 将请求级 `deep_thinking` 写入请求上下文，供 executor 在解析 fork 模型时读取。

#### Scenario: 流入口注入
- **WHEN** chat/stream 请求开始
- **THEN** 请求上下文记录该请求的 deep_thinking 值

### Requirement: fork 流空闲 watchdog

fork 子代理执行期间，若超过空闲阈值（默认 60s）无任何增量输出（reasoning/content/工具事件），系统 SHALL 中断子代理并按"空闲超时"处理。

#### Scenario: 持续输出不触发
- **WHEN** 子代理持续产生增量输出
- **THEN** 空闲计时持续重置，不中断

#### Scenario: 卡死不吐字触发
- **WHEN** 子代理超过 60s 无任何增量输出
- **THEN** 中断执行并记录超时原因=idle

### Requirement: fork 总时长保险丝

fork 子代理单次执行总时长 SHALL 有上限：默认 240s；请求 deep_thinking=true 时取 600s。达上限 SHALL 中断并记录超时原因=total。

#### Scenario: 关闭深思考的总时长
- **WHEN** deep_thinking=false 且 fork 执行超 240s
- **THEN** 中断并标记 total 超时

#### Scenario: 开启深思考放宽总时长
- **WHEN** deep_thinking=true
- **THEN** fork 总时长上限为 600s

### Requirement: fork turn 上限

fork 子代理 SHALL 有最大 agentic 轮次上限；上限取**执行者预设的 `maxTurns`**，未声明时回落系统默认 `DELEGATE_DEFAULT_MAX_TURNS`。上限与子代理的工具面无关（继承只读工具面后仍是同一条防御上限）。skill 的 `max-iterations` 声明**不参与**上限（字段已废弃）。

#### Scenario: 用默认上限

- **WHEN** 执行者预设未声明 `maxTurns`，且 fork 子代理轮次超过 `DELEGATE_DEFAULT_MAX_TURNS`
- **THEN** 停止执行并记录原因=turn

#### Scenario: 执行者预设声明上限

- **WHEN** 执行者预设声明 `maxTurns`
- **THEN** fork turn 上限取该声明值

#### Scenario: skill 声明 max-iterations 无效

- **WHEN** skill frontmatter 声明 `max-iterations`
- **THEN** 该声明不生效（加载期忽略并记 warning），上限仍按执行者预设 `maxTurns` / 系统默认取

### Requirement: fork 响应请求级取消（abort_signal）

fork 子代理执行 SHALL 响应请求级 `abort_signal`（cancel 端点置位）：置位后中断执行，中断原因=cancelled，与 idle/total/turn 中断同路径收敛（超时文案 + 日志 + 前端"分析中断"）。

#### Scenario: 请求取消中断 fork
- **WHEN** cancel 端点置位 ctx.abort_signal 且 fork 运行中
- **THEN** fork 中断并返回超时/取消文案，delegate end 原因=cancelled

#### Scenario: 取消与防失控共用路径
- **WHEN** fork 因 idle/total/turn 或取消任一机制中断
- **THEN** 均收敛为"分析中断（原因）"文案与 end 日志，原因使用同一枚举

### Requirement: 中断原因统一枚举

fork 结束原因 SHALL 使用单一枚举 `DelegateStopReason`（normal / idle / total / turn / failed / cancelled），集中定义于 const；`delegate end ok/reason` 与 task 注册表终态共用同一词表，契约/文档不得另起原因词。

#### Scenario: 原因词表一致
- **WHEN** delegate end 或 task 终态记录中断原因
- **THEN** 值取自 `DelegateStopReason`，不含枚举外字符串

### Requirement: 超时结果文案与日志

fork 因 idle/total/turn/cancelled 中断时，SHALL 返回统一超时文案给主 agent（引导基于现有上下文作答），并把中断原因写入 delegate 日志与前端过程区（可辨 idle/total/turn/cancelled）。

#### Scenario: 超时原因可辨
- **WHEN** fork 因任一超时机制中断
- **THEN** 日志与过程面板标记具体原因（idle/total/turn）
