# delegate-execution-controls Specification (Delta)

## MODIFIED Requirements

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
