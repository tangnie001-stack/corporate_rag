## ADDED Requirements

### Requirement: 稀疏支路贡献可见

混合检索每轮 SHALL 记录两路各自的贡献，使"某一支路是否真的在工作"无需人工比对即可发现。

该要求针对的失败形态：历史上融合后只记录融合结果总数，而两路的实际贡献不可见 —— 于是一个支路长期失效而日志照打"混合检索完成"，缺陷存活半个月无人发现（`trace_c54ce259`）。合并为单一存储后"某路半死而整体正常"的结构性原因消失了，但**贡献不可见这个观测缺口仍在**，需单独补上。

#### Scenario: 两路贡献被记录

- **WHEN** 一次混合检索完成
- **THEN** 日志 SHALL 同时反映 dense 路与词法路各自的贡献，使两路中任一路为 0 时可被直接看出

#### Scenario: 一路为空时可被发现

- **WHEN** 某一支路返回 0 条结果
- **THEN** 该事实 SHALL 出现在日志中，SHALL NOT 只记录融合后的总数

#### Scenario: 正常两路都有贡献时不产生告警

- **WHEN** 两路均返回非空结果并完成融合
- **THEN** SHALL NOT 产生任何降级或异常级别的日志

## MODIFIED Requirements

### Requirement: 生成层可观测（LLM 摘要事件，不进原文）

系统 SHALL 记录生成层摘要事件以支撑 L2 诊断，本期覆盖**主 agent 推理 + verify judge 边界**：主 agent 每轮推理（agent 主模型调用点）记 `[agent] model turn`（model / usage_in / usage_out / fallback / latency_ms / session_id / iteration）；verify 执行忠实度 judge 前 SHALL 记 `[verify] judge start`，结束后记 `[verify] judge done`（含 unsupported_count，judge 的 usage 本期不追）。judge / temporal / query_router 等其余 LLM 调用本期 SHALL NOT 添加摘要（演进路径：抽统一 LLM 调用层后由 `[llm] model call` 覆盖）。LLM 原始输出 SHALL NOT 进入默认日志；完整答案与引用 SHALL 经 SSE 回放（`/api/sessions/events`）与关系型库的会话表获取，需要原始 prompt/输出时经 `LLM_LOG_CONTENT` 开关临时开启。

#### Scenario: 态 A 不跑 judge 可正面断言

- **WHEN** 未绑定 KB 纯对话经过 verify
- **THEN** 该 trace 日志**不含** `[verify] judge start`（judge start 行成为"跑过 judge"的正面锚，便于 e2e 断言态 A 未执行 judge）

#### Scenario: 按 trace 还原生成摘要

- **WHEN** 排查一条回答质量问题时按 trace 检索
- **THEN** 可得到该请求的模型、usage、fallback、judge 结论等摘要，而答案原文经 SSE/会话表回放获取
