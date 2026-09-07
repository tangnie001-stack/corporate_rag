# delegate-observability Specification (Delta)

## ADDED Requirements

### Requirement: SSE 委派状态
系统 SHALL 仅在 fork 执行期间向前端推送委派状态（inline 命中不推）。

#### Scenario: 委派开始

- **WHEN** delegate_task 命中 fork skill 且子代理开始执行
- **THEN** 经 ctx 事件通道投递并推 SSEStatusEvent(stage=STAGE_DELEGATE, 文案="正在调用领域专家分析...")

#### Scenario: 委派结束

- **WHEN** fork 子代理完成
- **THEN** 推 SSEStatusEvent(stage=STAGE_DELEGATE, 文案="领域专家分析完成")

#### Scenario: inline 命中不推

- **WHEN** delegate_task 命中 inline skill（主 agent 自己答，无独立子代理）
- **THEN** 不推 STAGE_DELEGATE（避免"领域专家分析"误导）

#### Scenario: 常量归属

- **WHEN** 新增 STAGE_DELEGATE 与文案
- **THEN** 存于 src/config/const.py 的 SSEInteractionTexts（符合现有 stage/文案集中规范）

### Requirement: LLM 调用观测
系统 SHALL 让 fork 子代理的 LLM 调用进入现有观测体系。

#### Scenario: 观测继承

- **WHEN** fork 子代理用主 agent 的 llm 实例或 get_llm 新建实例执行
- **THEN** LLM 调用经实例自带 callbacks 记录（LlmContentLoggingHandler，与主 agent 同级观测）
- **AND** Langfuse 是否捕获取决于现有网关/实例接线，应用层不新增 Langfuse 专项（design D11）

#### Scenario: 不产生 MODEL_TURN

- **WHEN** fork 子代理内部逐轮调用 LLM
- **THEN** 不进入主 agent 的 MODEL_TURN 事件（与主 agent 同级观测，design D11）

### Requirement: 主 agent 迭代预算联动
系统 SHALL 保证 delegate 轮不影响主 agent 正常终止，且深度委派后留有整合余量。

#### Scenario: delegate 后整合余量

- **WHEN** 主 agent 本轮调用了 delegate_task
- **THEN** 该轮后的迭代上限临时放宽（+2 轮），允许主 agent 整合子代理结果
- **AND** 单请求总上限仍封顶（防 delegate 死循环）

#### Scenario: verify regen 复位预算

- **WHEN** verify 触发 regen（guardrails / regen_decision 的 regen dict 复位 _agent_iterations=0）
- **THEN** 同步复位 _delegate_used=False（regen = 全新 MAX_AGENT_ITERATIONS 预算，不被 delegate +2 放大）

#### Scenario: 正常终止不变

- **WHEN** 主 agent 未调 delegate_task
- **THEN** 迭代预算行为与现状完全一致（MAX_AGENT_ITERATIONS）

### Requirement: 委派装配可观测
系统 SHALL 在 delegate_task 未能装配时记录告警，避免主 agent 静默退回固定工具集。

#### Scenario: skills 目录缺失

- **WHEN** AgentService 装配时 skills 目录不存在（volume 未挂载/路径错）
- **THEN** 记 Event.DELEGATE_SKIP（app 层前缀，warning，reason=skills_dir_missing）
- **AND** delegate_task 不注册，主 agent 行为与现状一致（无委派能力但功能可用）

#### Scenario: 注册表为空

- **WHEN** skills 目录存在但无任何 skill（registry_empty）
- **THEN** 记 Event.DELEGATE_SKIP（reason=registry_empty）
- **AND** delegate_task 不注册（description 无可用 skill，注册无意义）
