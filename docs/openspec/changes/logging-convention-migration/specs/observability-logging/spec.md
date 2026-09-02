# observability-logging Specification

## Purpose

统一全系统日志规范：分层事件前缀 + 英文 k=v message + 五级级别语义，为检索质量诊断（P1 Change 2 的 agent 行为信号）提供可 grep、可聚合、可定位的日志基础。

## ADDED Requirements

### Requirement: 分层事件前缀

系统日志 SHALL 使用分层前缀标识事件归属（`[retrieval]` / `[verify]` / `[agent]` / `[session]` / `[db]` / `[llm]` 等），同一类事件全系统 SHALL 只有一个统一前缀与措辞，不得散落多种写法。

#### Scenario: 检索事件统一前缀

- **WHEN** 任何模块记录检索相关事件（发起/完成/失败/空结果）
- **THEN** 日志行 SHALL 以 `[retrieval]` 开头并带 k=v 字段（如 `[retrieval] search start query=... kb_id=...`），可用一条 grep 聚合全部检索事件

#### Scenario: 节点事件统一前缀

- **WHEN** agent 循环节点（verify/agent/format）记录自身事件
- **THEN** 日志行 SHALL 以对应节点名作为前缀段（如 `[verify] completeness missing=[2023]`）

### Requirement: message 英文 k=v

业务/技术事件日志的 message 文本 SHALL 使用英文小写 k=v 格式（`key=value` 空格分隔），不使用中文自由文本；中文 SHALL 仅用于展示给最终用户的文案（集中在 `SSEInteractionTexts`）。

#### Scenario: 中文仅限用户可见文案

- **WHEN** 记录一次内部事件（如检索、会话锁、DB 调用）
- **THEN** message 为英文 k=v（如 `[session] lock acquired session_id=...`），不出现中文叙述

### Requirement: 五级级别语义

系统日志 SHALL 按五级语义使用：`debug`（诊断细节，默认关闭）、`info`（正常流程里程碑）、`warning`（降级/可恢复异常，禁止用于"正常但少见"）、`error`（单点失败已处理）、`exception`（透传型异常，带完整 traceback）。与 rules.md 异常分类联动。

#### Scenario: warning 不滥用

- **WHEN** 某路径是正常但少见的执行分支
- **THEN** 使用 `info`（或 `debug`）而非 `warning` 记录，避免 warning 泛滥淹没真问题

### Requirement: 检索行为信号日志

系统 SHALL 提供 `retrieval_signal:` 前缀的结构化信号日志，用于在线诊断"哪条 query 检索质量差"。信号类型：`reretrieve`（同 turn 二次检索）/ `to_web`（转联网）/ `abstain_after_retrieve`（检索后拒答）/ `unsupported`（judge 无支撑）/ `cited`（正常引用，对照基线）/ `empty_result`（检索空）。行格式 SHALL 含 `signal` / `query` / `iteration` / `kb_id` 字段；trace_id SHALL 由日志框架自动注入（不写入 message）；信号埋点 SHALL 经统一 helper 收口（query 截断 40 字符、格式一致）。

#### Scenario: 定位检索差的问题 query

- **WHEN** 一条 query 在线链路触发 `to_web` 信号（绑定 KB 却走 search_web）
- **THEN** 日志出现 `retrieval_signal: signal=to_web query="..." iteration=N kb_id=... reason=...`，携带自动注入的 trace_id，可按 trace 回放该请求的检索全过程

#### Scenario: 信号格式统一

- **WHEN** 任意埋点记录行为信号
- **THEN** 均通过统一 helper 输出同构行（前缀 + k=v），不因埋点位置不同而格式漂移
