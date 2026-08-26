## ADDED Requirements

### Requirement: abstention 决策路径（全模型决策）

本 change 删除 classify 节点与固定流水线后，系统 SHALL 将空检索的 abstention 触发全部改为"模型决策"：`retrieve_kb` 返回空结果（或证据不足）时作为普通工具结果回喂模型，模型 SHALL 基于证据情况自行选择——输出 abstention 文案、调用 `ask_user` 追问、调用 `escalate_to_human` 转人工、或基于已有证据作答。不再存在"确定性 abstention 分支"。

#### Scenario: 空检索模型决策
- **WHEN** retrieve_kb 返回空结果
- **THEN** 模型收到空工具结果后自行决定 abstain / 追问 / 转人工，不由流水线硬编码判断

#### Scenario: KB 未解析 → 语义选库检索
- **WHEN** kb_router 未解析出知识库（如无 user_id）
- **THEN** retrieve_kb 以语义匹配 query 与各 KB 的 name+description，选中相似度最高的 1 个知识库进行检索；匹配失败（无 KB 或相似度低于阈值）时返回空工具结果，模型按 abstain / ask_user / escalate 决策，不触发旧确定性 abstention 文案
