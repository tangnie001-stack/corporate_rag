# delegate-task Specification (Delta)

> 本 delta 的 MODIFIED 块**已 rebase 到 `skill-execution-and-delegation` 落地后的主规格文本**（2026-09-28，其归档提交 `c6c8ea8` 已把该 requirement 的工具面口径同步进在效主 spec）。本节**仅改动 `生成子代理` 的 middleware 那一条**，其余逐字保留。

## MODIFIED Requirements

### Requirement: fork 执行

系统 SHALL 让 context=fork 的 skill 走子代理路径。

#### Scenario: 生成子代理

- **WHEN** delegate_task 命中 fork skill
- **THEN** 用 `langchain.agents.create_agent` 生成独立子代理（**不使用已废弃的 `langgraph.prebuilt.create_react_agent`**）
- **AND** 子代理 system_prompt = 执行者人设（优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > 系统默认 prompt）
- **AND** 子代理初始 user message = skill 内容（任务/方法论）；由 `/xxx` 触发时，`/` 后的剩余文本作为输入
- **AND** 子代理工具面 = **本轮主 agent 启用工具集** − 禁用集（`FORK_FORBIDDEN_TOOLS` + 主 agent 专属工具类）− **非只读工具**（依 `readonly_map()` 判定；表中缺项按非只读处理，不下发）∩（skill 声明 `allowed-tools` 时）allowed-tools ∩（执行者预设声明 `tools` 时）preset.tools
- **AND** `allowed-tools` 的语义是**收窄项**：不声明即不收窄（但仍受禁用集与只读约束），SHALL NOT 因未声明而退化为零工具；**显式声明可放行非只读工具**（白名单退为例外通道）
- **AND** 子代理使用独立 RequestContext（独立的 `tool_contexts` / 引用编号；`pending_asks` 为进程级按 session 的单槽、**不随子上下文复制**），不污染主 agent
- **AND** 子代理最大轮次取执行者预设的 `maxTurns`（未声明则用系统默认上限）——其**取值与归属见 `delegate-execution-controls` 的「fork turn 上限」**；本变更 SHALL NOT 把它改由图内回合预算 middleware 强制（见 `agent-assembly` 的「回合上限由装配参数决定并可动态放宽」的适用范围）
- **AND** 子代理 SHALL 经由与主 agent **共用的装配入口**生成（见 `agent-assembly` 的「主/子角色共用同一装配入口」）；主角色装配 system 施加 / 模型参数 / 回合预算 / 观测四件套，子角色的 **middleware 集合为空**（其模型轮次记录由 fork 委派的事件消费侧承担，图内 SHALL NOT 重复产出主循环口径的轮次日志与模型观测）。原先「`create_agent` 的 middleware 参数保留装配位但默认传空（v1 不启用）」的表述自本变更起不再成立

#### Scenario: 工具面缺省为继承

- **WHEN** 一个 fork skill 未声明 `allowed-tools`，执行者预设也未声明 `tools`
- **THEN** 子代理拿到与主 agent 相同的**只读**工具面（减去禁用集与非只读工具），可自主检索
- **AND** 不产生"零工具告警"（该告警的语义已作废）

#### Scenario: 非只读工具默认不下发

- **WHEN** 主 agent 工具面中存在非只读工具（依 `readonly_map()`；表中**缺项**按非只读处理），且 skill 未在 `allowed-tools` 中显式声明它
- **THEN** 该工具不出现在子代理工具面
- **AND** 理由：子代理（尤其 `/xxx` 直出，其调用早于任何用户确认）不得自动获得写权限；写权限必须由 skill 显式声明

#### Scenario: 只读表为空时的极性（与双轴推导不同，须写明）

- **WHEN** 装配 fork 工具面时 `readonly_map()` 为空（工具尚未注册）
- **THEN** 按 **fail-closed** 处理（不下发任何工具）并记 warning——与 `derive_invocation_flags` 对空表的 **fail-open**（`src/agents/skills/invocation.py:33-36`）**极性相反**
- **AND** 该差异是**有意为之**：同一张表的两个消费者失败代价不同——双轴推导空表时不锁只是少了一层保护，而 fork 侧空表时"按只读放行"会把写权限下发给子代理。实施者不得为"统一"而改掉任一极
