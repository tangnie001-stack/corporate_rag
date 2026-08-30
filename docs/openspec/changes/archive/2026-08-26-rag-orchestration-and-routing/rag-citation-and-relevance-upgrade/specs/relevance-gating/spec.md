# relevance-gating Specification

## Purpose
定义检索结果相关度门控与低相关度时的 abstention 出口：低于阈值的 context 不得送入 LLM，无达标 context 时走"未找到相关数据"专用回答路径。

## ADDED Requirements

### Requirement: Rerank 分数阈值
系统 SHALL 在 rerank 精排后应用可配置的最低分数阈值，低于阈值的 context 不得进入生成阶段。阈值默认 0.3，可通过环境变量 `RERANK_MIN_SCORE` 覆盖。

#### Scenario: 低分 context 被过滤
- **WHEN** rerank 后某个 context 的 relevance_score 低于阈值
- **THEN** 该 context SHALL 被丢弃，不进入生成阶段的 prompt

#### Scenario: 阈值可配置
- **WHEN** 用户设置 `RERANK_MIN_SCORE=0.5` 环境变量
- **THEN** 系统 SHALL 使用 0.5 作为过滤阈值

### Requirement: 全部低于阈值时走 abstention
当精排后没有任何 context 达到阈值时，系统 SHALL 走 abstention 分支：回答明确输出"未在文档中找到相关数据"，且不展示任何引用来源。

#### Scenario: 全部低分触发拒答
- **WHEN** rerank 后所有 context 分数均低于阈值（contexts 为空）
- **THEN** 回答 SHALL 为"未在文档中找到相关数据"类文案，引用列表 SHALL 为空

#### Scenario: 检索结果为空
- **WHEN** 检索阶段返回 0 条结果
- **THEN** 系统 SHALL 走 abstention 分支，不发起生成调用

### Requirement: 降级路径整合
当 grader 重试耗尽触发降级（`downgraded=true`）时，系统 SHALL 仍然应用分数阈值；若降级后仍无达标 context，则走 abstention 分支，不得退回无上下文的 Naive RAG 生成。

#### Scenario: 降级后仍无达标 context
- **WHEN** grader 重试耗尽降级，且 rerank 后无 context 达标
- **THEN** 回答 SHALL 走 abstention 分支

#### Scenario: 降级但有达标 context
- **WHEN** grader 重试耗尽降级，但 rerank 后有 context 达标
- **THEN** 系统 SHALL 使用达标 context 正常生成回答并展示对应引用

### Requirement: 状态与事件透出
系统 SHALL 在 abstention 分支执行时，通过 SSE 向前端透出明确的状态提示（如"未找到相关文档"），并保证流以 done 事件正常结束。

#### Scenario: 前端收到 abstention 提示
- **WHEN** 系统走 abstention 分支
- **THEN** 前端 SHALL 收到对应的状态事件和 done 事件，且不收到任何 citation 事件
