## ADDED Requirements

### Requirement: 检索结果统一类型

检索链路（ChromaDB / BM25 / RRF fusion / Reranker）之间传递的数据 SHALL 使用 `ChunkResult` dataclass 替代 `list[dict]`。

#### Scenario: similarity_search 返回 ChunkResult
- **WHEN** VectorStore.similarity_search() 完成查询
- **THEN** 返回 `list[ChunkResult]`，每项含 id、content、metadata、distance 字段

#### Scenario: BM25 search 返回 ChunkResult
- **WHEN** BM25Index.search() 完成查询
- **THEN** 返回 `list[ChunkResult]`，每项含 id、content、metadata、bm25_score 字段

#### Scenario: RRF fusion 输入/输出 ChunkResult
- **WHEN** rrf_fusion() 接收 dense 和 bm25_res 参数
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
- **THEN** 返回 `list[ChunkResult]`（distance/bm25_score 为 None）

#### Scenario: get_chunks_paginated 返回 ChunkQueryResult
- **WHEN** VectorStore.get_chunks_paginated() 完成查询
- **THEN** 返回 `ChunkQueryResult`（含 items:list[ChunkResult]、total、page、page_size）

### Requirement: RAGContext 启用 slots

RAGContext SHALL 启用 `@dataclass(slots=True)`，与新增 dataclass 保持一致性，同时优化内存占用和属性访问性能。

#### Scenario: RAGContext 为 slots dataclass
- **WHEN** 导入 RAGContext
- **THEN** 应使用 `@dataclass(slots=True)`

### Requirement: MySQL 实体类型

每个 MySQL 表的查询结果 SHALL 使用对应的 dataclass Entity 类型，而非 raw dict。

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

### Requirement: AgentState 存放 dataclass

LangGraph 的 AgentState SHALL 直接存放 dataclass 对象，不做 JSON 序列化适配。

#### Scenario: retrieval_results 为 list[ChunkResult]
- **WHEN** retrieve_node 向 state 写入 retrieval_results
- **THEN** retrieval_results 类型为 `list[ChunkResult]`
- **WHEN** grader_node 从 state 读取 retrieval_results
- **THEN** 可直接访问 `.content` 而非 `.get("content")`

#### Scenario: contexts 为 list[RAGContext]
- **WHEN** rerank_node 向 state 写入 contexts
- **THEN** contexts 类型为 `list[RAGContext]`（非 dict 列表）
- **WHEN** generate_node 从 state 读取 contexts
- **THEN** 无需 `RAGContext(**c)` 转换

### Requirement: vector_store.py 拆为模块包

VectorStore 类 SHALL 拆分为模块包，按职责分文件。

#### Scenario: 包结构
- **WHEN** 导入 VectorStore
- **THEN** 应从 `src.infra.db.vector_store` 导入
- **WHEN** 查看 vector_store 目录
- **THEN** 包含 `__init__.py`、`embedding.py`、`client.py`、`store.py`、`search.py`

#### Scenario: 接口不变
- **WHEN** 外部代码调 VectorStore 方法
- **THEN** 方法名和参数签名不变，仅返回类型从 dict 改为 dataclass

### Requirement: mysql_db.py 拆为 Repo

MySQLDB 类 SHALL 拆分为连接管理 + 5 个 Domain Repo。

#### Scenario: 连接池在 MySQLDB
- **WHEN** 应用启动
- **THEN** MySQLDB 仅管理连接池和 init_db
- **WHEN** 调用 Repo 方法
- **THEN** Repo 复用 MySQLDB 的连接池，不创建新连接

#### Scenario: 5 个 Domain Repo
- **WHEN** 需要操作知识库
- **THEN** 通过 KbRepo 访问
- **WHEN** 需要操作文档
- **THEN** 通过 DocumentRepo 访问
- **WHEN** 需要操作会话/消息
- **THEN** 通过 ChatRepo 访问
- **WHEN** 需要操作用户
- **THEN** 通过 UserRepo 访问
- **WHEN** 需要操作评估报告
- **THEN** 通过 EvalRepo 访问

### Requirement: api/documents.py 走 service

文档 chunk 查询 SHALL 通过 AppService 暴露，不直接调 vector_store。

#### Scenario: chunk 查询走 service
- **WHEN** POST /api/kbs/documents/chunks 被调用
- **THEN** api 层调 AppService 方法，不直接调 vector_store

### Requirement: ChatManager 改用 ChatRepo

PersistenceService SHALL 接收 ChatRepo 而非 MySQLDB。

#### Scenario: PersistenceService 注入 ChatRepo
- **WHEN** PersistenceService 被初始化
- **THEN** 参数为 ChatRepo 而非 MySQLDB
- **WHEN** save_session() 被调用
- **THEN** 内部调 chat_repo.create_session()
- **WHEN** save_message() 被调用
- **THEN** 内部调 chat_repo.save_message()
