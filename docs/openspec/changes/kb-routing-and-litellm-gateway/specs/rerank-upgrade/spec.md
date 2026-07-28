## ADDED Requirements

### Requirement: Rerank 模型升级
系统 SHALL 将 Rerank 模型从 `gte-rerank-v1` 升级为 `qwen3-rerank`。

#### Scenario: 创建 Rerank 实例
- **WHEN** 调用 `get_rerank()`
- **THEN** 使用的模型名为 `qwen3-rerank`（配置项 `RERANK_MODEL`）
- **THEN** 调用 DashScope Rerank API

#### Scenario: 功能正常
- **WHEN** Rerank 模型处理检索结果
- **THEN** 返回精排后的文档列表，按相关性降序排列
- **THEN** 返回结果的行为与升级前一致（接口不变）
