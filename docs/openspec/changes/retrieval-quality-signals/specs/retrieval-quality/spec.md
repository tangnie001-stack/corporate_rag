# retrieval-quality Specification (Delta)

## ADDED Requirements

### Requirement: 在线检索质量诊断（agent 行为信号）

系统 SHALL 通过 agent 行为信号在线观测检索质量，不引入额外 LLM grader：agent 对检索结果的"不满意行为"（同轮换词重检、绑定 KB 仍转联网、检索后拒答、judge 无支撑、检索空）作为检索质量差的判据。信号 SHALL 仅在会话绑定 KB（检索真实发生）时激活；未绑定 KB 的纯对话不产生检索质量信号（联网为主路径、空检索为设计行为）。信号经统一 helper 以 `retrieval_signal:` 前缀输出，携带 query / iteration / kb_id，trace_id 由日志框架注入。

#### Scenario: 绑定 KB 检索不足转联网产生信号

- **WHEN** 会话绑定 KB，agent 自主经检索降级路径调 search_web（先 retrieve_kb 且结果空/无关，按 prompt 规则换词再检仍无后转联网）
- **THEN** 日志产生 `retrieval_signal: signal=to_web query=... iteration=... kb_id=...`，可按 trace 回放该 query 检索全过程

#### Scenario: verify 完整性补数据联网不产生 to_web 信号

- **WHEN** search_web 由 verify 节点指派（完整性检测缺失年份 → 用户确认联网 → 注入指引驱动，`ctx.web_guided` 已置位）
- **THEN** 不产生 to_web 检索缺陷信号（该路径属知识库数据覆盖不足，非检索质量问题；search_web 读 ctx.web_guided 判定排除）

#### Scenario: 未绑定 KB 联网不产生缺陷信号

- **WHEN** 会话未绑定 KB（纯对话），agent 直接调 search_web 回答
- **THEN** 不产生 to_web / empty_result 检索质量缺陷信号（联网是主路径非降级）

#### Scenario: 纯对话检索空不标缺陷

- **WHEN** 会话未绑定 KB，retrieve_kb 返回空
- **THEN** 不产生 empty_result 信号（未绑定即不检索，空是设计行为）

#### Scenario: 对照基线正常引用

- **WHEN** 答案正常带 [n] 引用
- **THEN** 产生 `signal=cited` 对照基线信号，引用 kind 区分 kb/web

### Requirement: 检索去重策略参数化

系统 SHALL 将检索结果按 doc_id 去重的策略参数化（每文档保留条数可配置，`RETRIEVAL_MAX_PER_DOC`，默认 1 保持现状），支持多样性对照实验评估最优值。

#### Scenario: 每文档保留多条的多样性对照

- **WHEN** 配置 `RETRIEVAL_MAX_PER_DOC=2` 重新评估同一小测试集
- **THEN** 检索结果保留每文档最多 2 条参与 rerank，与默认 1 条的结果可用 RAGAS 指标对照

#### Scenario: 默认行为不回归

- **WHEN** 未配置 `RETRIEVAL_MAX_PER_DOC`（保持默认 1）
- **THEN** 检索去重行为与现状一致（每文档最先出现的结果保留）
