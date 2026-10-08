# context-budget Specification

## Purpose
TBD - created by archiving change context-window-and-budget-foundation. Update Purpose after archive.
## Requirements
### Requirement: 历史预算 SHALL 是集中的绝对 token 量

历史裁剪预算 SHALL 由一个集中的绝对 token 量常量定义（`src/config/`），SHALL NOT 使用「窗口 × 比例」作为主口径，也 SHALL NOT 在函数签名上出现写死的默认值。

取值 SHALL 与模型窗口**解耦**——本能力不读取任何窗口数值。

#### Scenario: 预算来自集中常量

- **WHEN** 计算跨轮历史的可用预算
- **THEN** 预算 SHALL 等于配置的绝对 token 量
- **AND** 代码中 SHALL NOT 存在「窗口 × 比例」或写死的窗口默认值参与该计算

#### Scenario: 轮数粗筛仍先于预算细裁

- **WHEN** 历史消息多于轮数粗筛上限
- **THEN** 系统 SHALL 先按轮数取最近若干轮
- **AND** 再按绝对预算从最旧逐条裁剪

### Requirement: 历史裁剪结果 SHALL 落在预算内

**注入的跨轮历史总量**——即「**摘要段**（若存在） + 保留的尾部」——SHALL 不超过配置的绝对预算（最近 1 轮完整保留的既有例外除外，且该例外 SHALL 被显式记录）。摘要段 SHALL NOT 因不在裁剪路径上而绕过该口径；超限时系统 SHALL 先缩减摘要，仍超再裁剪尾部。

#### Scenario: 裁剪后不超预算

- **WHEN** 对一段超出预算的历史执行裁剪
- **THEN** 裁剪结果的总 token 量 SHALL ≤ 配置预算
- **AND** 返回新列表，不修改入参

#### Scenario: 摘要段计入预算

- **WHEN** 组装「摘要段 + 保留尾部」
- **THEN** 其聚合 token 量 SHALL ≤ 配置预算
- **AND** 超限时 SHALL 先缩减摘要、仍超再裁剪尾部
- **AND** SHALL NOT 因「摘要段在裁剪之后追加」而跳过该约束

#### Scenario: 最近一轮的保留例外被显式记录

- **WHEN** 即使仅保留最近 1 轮也会超过预算
- **THEN** 系统 SHALL 完整保留最近 1 轮
- **AND** SHALL 记录该例外（含预算与实际用量），SHALL NOT 静默超出

### Requirement: 单轮累积输入 SHALL 被度量并可观测

系统 SHALL 在模型调用前度量**当前轮累积的输入 token**；当该值达到配置的上限时，SHALL 记录可观测信号（含 `used` 与 `limit`）。

本要求 SHALL NOT 被理解为「已能把上下文压下来」：压缩能力属摘要层（后续 change）。本阶段对超限的处置**仅为度量与记录**，不做截断、不做摘要。

#### Scenario: 轮内累积被度量

- **WHEN** 单轮内发生模型调用
- **THEN** 系统 SHALL 计算该次调用输入消息的总 token 量
- **AND** 达上限时 SHALL 记录含 `used` / `limit` 的日志

#### Scenario: 超限不改变内容

- **WHEN** 单轮累积达到上限
- **THEN** 系统 SHALL NOT 因该度量而截断、删除或改写任何消息
- **AND** SHALL NOT 声称超限已被解决

### Requirement: 度量与观测失败 SHALL 降级且不中断请求

度量、计数或日志记录失败时，系统 SHALL 精确捕获异常、记录 warning，并**原样放行**本次调用。

#### Scenario: 计数失败不影响请求

- **WHEN** token 计数或日志记录抛出异常
- **THEN** 系统 SHALL 记 warning 并继续执行
- **AND** SHALL NOT 把本次工具/模型调用变成错误
