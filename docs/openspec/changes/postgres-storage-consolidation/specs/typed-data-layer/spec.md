## MODIFIED Requirements

### Requirement: 检索结果统一类型

检索链路（dense 路 / 词法路 / RRF fusion / Reranker，两路同源于一个 PostgreSQL 实例）之间传递的数据 SHALL 使用 `ChunkResult` dataclass 替代 `list[dict]`。

`ChunkResult` SHALL 携带两路各自的排名字段（未出现在某一路时标记为缺席），并使用与引擎无关的字段名表达词法路得分（`lexical_score`，SHALL NOT 沿用会误导为 BM25 的旧名）。

#### Scenario: similarity_search 返回 ChunkResult
- **WHEN** VectorStore.similarity_search() 完成查询
- **THEN** 返回 `list[ChunkResult]`，每项含 id、content、metadata、distance 字段

#### Scenario: 词法检索返回 ChunkResult
- **WHEN** 词法检索完成查询
- **THEN** 返回 `list[ChunkResult]`，每项含 id、content、metadata、lexical_score 字段

#### Scenario: RRF fusion 输入/输出 ChunkResult
- **WHEN** rrf_fusion() 接收两路结果参数
- **THEN** 两个参数均为 `list[ChunkResult]`
- **WHEN** rrf_fusion() 返回融合结果
- **THEN** 返回 `list[ChunkResult]`

#### Scenario: rerank_results 输入 ChunkResult
- **WHEN** rerank_results() 接收 results 参数
- **THEN** results 为 `list[ChunkResult]`

#### Scenario: similarity_search_all 返回 ChunkResult
- **WHEN** VectorStore.similarity_search_all() 完成查询
- **THEN** 返回 `list[ChunkResult]`

#### Scenario: get_chunks_by_doc_id 返回 ChunkResult
- **WHEN** VectorStore.get_chunks_by_doc_id() 完成查询
- **THEN** 返回 `list[ChunkResult]`（distance/lexical_score 为 None）

#### Scenario: get_chunks_paginated 返回 ChunkQueryResult
- **WHEN** VectorStore.get_chunks_paginated() 完成查询
- **THEN** 返回 `ChunkQueryResult`（含 items:list[ChunkResult]、total、page、page_size）

#### Scenario: 融合结果保留分路排名
- **WHEN** 一次混合检索完成融合
- **THEN** 每条结果 SHALL 可读出其在 dense 路与词法路的排名，SHALL NOT 只保留融合后的单一分数

### Requirement: 关系型实体类型

每个关系型表的查询结果 SHALL 使用对应的 dataclass Entity 类型，而非 raw dict。

#### Scenario: 知识库查询返回 KbListItem
- **WHEN** KbRepo.get_all_kb() 被调用
- **THEN** 返回 `list[KbListItem]`，每项含 id、user_id、name、doc_count

#### Scenario: 知识库查询返回 KbEntity 或 str
- **WHEN** KbRepo.get_or_create_kb() 被调用
- **THEN** 返回 `tuple[str, bool]`（kb_id, is_new）
- **WHEN** KbRepo.get_kb_by_name() 被调用
- **THEN** 返回 `Optional[str]`

#### Scenario: 文档查询返回 DocEntity
- **WHEN** DocumentRepo.get_documents() 被调用
- **THEN** 返回 `list[DocEntity]`

#### Scenario: 会话查询返回 SessionEntity
- **WHEN** ChatRepo.get_session_by_id() 被调用
- **THEN** 返回 `Optional[SessionEntity]`

#### Scenario: 会话列表返回 SessionListItem
- **WHEN** ChatRepo.get_sessions() 被调用
- **THEN** 返回 `list[SessionListItem]`

#### Scenario: 消息查询返回 MessageEntity
- **WHEN** ChatRepo.get_messages() 被调用
- **THEN** 返回 `list[MessageEntity]`

#### Scenario: 用户查询返回 UserEntity
- **WHEN** UserRepo.get_user_by_account() 被调用
- **THEN** 返回 `Optional[UserEntity]`
- **WHEN** UserRepo.get_user_by_token() 被调用
- **THEN** 返回 `Optional[UserEntity]`
