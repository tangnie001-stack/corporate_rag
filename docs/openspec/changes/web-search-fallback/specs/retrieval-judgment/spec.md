# retrieval-judgment Specification

## Purpose
TBD - created by archiving change web-search-fallback. Update Purpose after archive.

## Requirements

### Requirement: 一律先检索

非闲聊实质性问题 SHALL 一律先调用 `retrieve_kb` 检索知识库，根据检索结果判断能否回答，不预先猜测问题是否在知识库范围内。

#### Scenario: 非闲聊先检索
- **WHEN** 用户提出实质性查询
- **THEN** agent SHALL 调用 retrieve_kb，而不是凭记忆或常识直接作答

#### Scenario: 闲聊直接答
- **WHEN** 用户提出闲聊/问候
- **THEN** agent SHALL 直接回答，不调用检索工具

### Requirement: 内容判定标准

模型 SHALL 通过阅读检索结果内容判定相关性，判定标准为"chunk 内容含 query 至少一个核心实体才算相关"；rerank 分数 SHALL 仅用于排序，不做绝对阈值判定。

#### Scenario: 含核心实体判定相关
- **WHEN** 检索 chunk 内容含 query 的核心实体
- **THEN** 判定为相关，按文档内容作答并引用

#### Scenario: 无关内容判定不相关
- **WHEN** 检索 chunk 内容与 query 核心实体无关（如问"阿里云"返回股东持股内容）
- **THEN** 判定为不相关，走换词再检或联网兜底

### Requirement: 换词再检召回兜底

检索结果为空或全部明显不相关时，模型 SHALL 提炼核心实体换一种问法再次调用 `retrieve_kb`，第二枪 `top_k` 加大以降低"相关内容排在后面未被看到"的概率。

#### Scenario: 换词再检
- **WHEN** 第一次 retrieve_kb 结果为空或全部明显不相关
- **THEN** 模型 SHALL 提炼核心实体后再次调用 retrieve_kb（top_k 加大）

#### Scenario: 仍无结果转兜底
- **WHEN** 换词再检仍无相关结果
- **THEN** 判定为不在知识库范围内，调用 search_web 兜底；web 搜索关闭或失败时走纯拒答

### Requirement: 检索结果去重

检索结果 SHALL 按 doc_id 去重（保留每个文档最先出现的结果），去重位置在 RRF 融合后、rerank 前，保证候选多样性，避免重复文档占满检索窗口。

#### Scenario: 重复文档去重
- **WHEN** 同一文档存在多个副本（重复入库）
- **THEN** 检索结果 SHALL 只保留该文档最先出现的结果，去重后再执行 rerank
