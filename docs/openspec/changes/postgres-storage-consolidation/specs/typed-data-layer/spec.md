## REMOVED Requirements

### Requirement: MySQL 实体类型

**Reason**: 该 requirement 的标题以具体引擎（MySQL）为限定，引擎替换为 PostgreSQL 后不再成立。其正文内容（每个表的查询结果用对应 dataclass Entity 而非 raw dict）本身仍然有效，只是需要引擎中立化。

**Migration**: 见本 delta 的 `## ADDED Requirements` 中的「关系型实体类型」—— 同一要求的引擎中立化版本，scenario 与原要求一一对应。

### Requirement: mysql_db.py 拆为 Repo

**Reason**: 该 requirement 点名的 `MySQLDB` 类**在代码中已不存在**（仅 `src/chat/manager.py:82,134` 两处 docstring 残留），且引擎替换后该命名不再成立。该 requirement 早已是历史陈述。

**Migration**: 见本 delta 的 `## ADDED Requirements` 中的「连接管理拆为 Repo」—— 保留「连接管理与表操作分离 + 5 个 Domain Repo」的实质要求，去掉已不存在的类名。

## MODIFIED Requirements

### Requirement: 检索结果统一类型

检索链路（dense 路 / 词法路 / RRF fusion / Reranker，两路同源于一个 PostgreSQL 实例）之间传递的数据 SHALL 使用 `ChunkResult` dataclass 替代 `list[dict]`。

`ChunkResult` SHALL 携带两路各自的排名字段（未出现在某一路时标记为缺席），并使用与引擎无关的字段名表达词法路得分（`lexical_score`，SHALL NOT 沿用会误导为 BM25 的旧名）。

**`ChunkResult.metadata` SHALL 在读取时由列值与 jsonb 平铺合并回填**（冲突以列为准），至少含 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page`。

该回填不是可选项：去重读 `metadata["doc_id"]`、引用渲染读 `metadata["source"/"page"/"doc_id"]`、实体透传读实体键 —— 这三处都只从 `metadata` 取值。若这些键被搬到列上而不回填，去重会静默失效（全部结果走"无 doc_id 则保留"分支）、引用与来源变空，且都不报错。

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

#### Scenario: get_chunks_by_doc_id 返回 ChunkResult
- **WHEN** VectorStore.get_chunks_by_doc_id() 完成查询
- **THEN** 返回 `list[ChunkResult]`（distance/lexical_score 为 None）

#### Scenario: get_chunks_paginated 返回 ChunkQueryResult
- **WHEN** VectorStore.get_chunks_paginated() 完成查询
- **THEN** 返回 `ChunkQueryResult`（含 items:list[ChunkResult]、total、page、page_size）

#### Scenario: 融合结果保留分路排名
- **WHEN** 一次混合检索完成融合
- **THEN** 每条结果 SHALL 可读出其在 dense 路与词法路的排名，SHALL NOT 只保留融合后的单一分数

#### Scenario: metadata 契约键齐全
- **WHEN** 任一路径返回 `ChunkResult`
- **THEN** 其 `metadata` SHALL 含 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page`，使去重、引用与实体透传无需改动即可继续工作

#### Scenario: 全局检索路径已移除
- **WHEN** 检索被调用
- **THEN** SHALL NOT 存在不指定知识库的全局检索路径（该路径在生产链路不可达）

### Requirement: ChatManager 改用 ChatRepo

PersistenceService SHALL 接收 ChatRepo 而非关系型连接组件。

#### Scenario: PersistenceService 注入 ChatRepo
- **WHEN** PersistenceService 被初始化
- **THEN** 参数为 ChatRepo 而非关系型连接组件
- **WHEN** save_session() 被调用
- **THEN** 内部调 chat_repo.create_session()
- **WHEN** save_message() 被调用
- **THEN** 内部调 chat_repo.save_message()

## ADDED Requirements

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

### Requirement: 连接管理拆为 Repo

关系型连接管理 SHALL 拆分为连接/会话生命周期 + 5 个 Domain Repo；SHALL NOT 存在一个同时持有连接池与全部表操作的单一类。

#### Scenario: 连接池只负责连接

- **WHEN** 应用启动
- **THEN** 连接/会话工厂 SHALL 仅管理连接池与会话生命周期
- **WHEN** 调用 Repo 方法
- **THEN** Repo SHALL 复用该连接池，不创建新连接

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
