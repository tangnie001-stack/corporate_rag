## ADDED Requirements

### Requirement: API 响应体日志（非 GET）

系统 SHALL 对所有非 GET 请求（POST/PUT/DELETE）记录响应体内容，前缀格式为 `[API]`。

#### Scenario: POST 接口返回时记录响应体
- **WHEN** 用户发起 POST 请求且接口正常返回
- **THEN** 日志记录 `[API] {method} {path} | status={status_code} | data={response_data}`
- **THEN** 日志中包含当前 traceid

#### Scenario: GET 接口不记录响应体
- **WHEN** 用户发起 GET 请求
- **THEN** 不记录响应体日志

#### Scenario: SSE 流式接口不记录
- **WHEN** 请求路径为 `/api/chat/stream`
- **THEN** 不记录响应体日志

#### Scenario: API_SKIP_FULL_LOG 名单中的路由跳过全量响应体
- **WHEN** 请求路径在 `API_SKIP_FULL_LOG` 集合中
- **THEN** 只记录 `[API] {method} {path} | status={code} | data=<skipped>`

#### Scenario: 响应体超过 10MB 截断
- **WHEN** 响应体体积超过 10MB
- **THEN** 记录截断后的内容，并在末尾标注截断信息

---

### Requirement: MySQL SQL 返回值日志

系统 SHALL 对所有 SELECT 查询记录返回数据内容，前缀格式为 `[SQL]`。
特定方法（如 `get_messages`）可通过 `SQL_SKIP_FULL_LOG` 集合跳过全量记录，仅记录 count。
该集合定义在 `src/core/logging.py` 中，与 `API_SKIP_FULL_LOG` 集中管理。

#### Scenario: SELECT 查询返回多行
- **WHEN** `get_sessions`、`get_documents`、`get_all_kb` 等返回列表的方法执行
- **THEN** 日志记录 `[SQL] method={name} | rows={count} | data={rows}`

#### Scenario: SELECT 查询返回单行
- **WHEN** `get_session_by_id`、`get_user_by_account`、`get_user_by_token`、`get_kb_by_name` 等返回单行的方法执行
- **THEN** 日志记录 `[SQL] method={name} | rows=1 | data={row}`

#### Scenario: SQL 返回值为 None
- **WHEN** 查询结果不存在（返回 None 或空列表）
- **THEN** 日志记录 `[SQL] method={name} | rows=0 | data=None`

#### Scenario: get_messages 只记录 count
- **WHEN** `get_messages` 执行
- **THEN** 日志记录 `[SQL] method=get_messages | session_id={id} | count={n}`（不记录 content）

#### Scenario: SQL 返回值超过 10MB
- **WHEN** 返回值体积超过 10MB
- **THEN** 记录截断后的内容，并在末尾标注截断信息

#### Scenario: get_or_create_kb 的两个返回路径
- **WHEN** 新建知识库返回 `(kb_id, True)`
- **THEN** 日志记录创建路径的返回结果
- **WHEN** 已存在知识库返回 `(existing_id, False)`
- **THEN** 日志记录已存在路径的返回结果

---

### Requirement: ChromaDB 检索结果日志

系统 SHALL 对 ChromaDB 的检索和查询操作记录返回数据，前缀格式为 `[CHROMA]`。

#### Scenario: similarity_search 返回检索结果
- **WHEN** `similarity_search` 执行并返回结果
- **THEN** 日志记录 `[CHROMA] method=similarity_search | kb_id={id} | rows={count} | data={chunks}`

#### Scenario: similarity_search_all 返回全局检索结果
- **WHEN** `similarity_search_all` 执行并返回结果
- **THEN** 日志记录 `[CHROMA] method=similarity_search_all | rows={count} | data={results}`

#### Scenario: get_chunks_by_doc_id 返回分块列表
- **WHEN** `get_chunks_by_doc_id` 执行
- **THEN** 日志记录 `[CHROMA] method=get_chunks_by_doc_id | data={chunks}`

#### Scenario: get_chunks_paginated 返回分页分块
- **WHEN** `get_chunks_paginated` 执行
- **THEN** 日志记录 `[CHROMA] method=get_chunks_paginated | page={n} | data={items}`

---

### Requirement: 日志文件配置

系统 SHALL 仅输出日志到文件，不输出到控制台。
所有文件 sink 使用异步写入（`enqueue=True`）。

#### Scenario: 移除控制台日志
- **WHEN** 应用启动
- **THEN** 控制台不输出日志

#### Scenario: ERROR 级别日志独立文件
- **WHEN** 使用 `logger.error` 记录日志
- **THEN** 日志同时写入 `logs/app_{date}.log` 和 `logs/error_{date}.log`
- **THEN** 两个文件 sink 均使用 `enqueue=True` 异步写入

#### Scenario: 日志轮转和保留
- **WHEN** 日志文件超过一天
- **THEN** 自动轮转生成新文件
- **THEN** 超期 7 天（app.log）/ 30 天（error.log）的日志文件自动删除
