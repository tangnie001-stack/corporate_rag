# Comet Design Handoff

- Change: session-history
- Phase: design
- Mode: compact
- Context hash: 89ba5c37884ca62492bc77285bfcbe19185596af34fec26f3d0d70c982333c04

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/session-history/proposal.md

- Source: openspec/changes/session-history/proposal.md
- Lines: 1-36
- SHA256: f3c012150ba6c1319aa9b08fd1101d2b6bde836c64499370797432dd8ceaacf9

```md
## Why

聊天页面目前没有会话历史管理功能，左侧侧边栏展示的是知识库列表。用户每次刷新页面后对话丢失，也无法回溯之前的对话。需要支持会话历史的展示、切换和管理，让用户能像主流 AI 聊天应用一样查看和管理自己的对话记录。

## What Changes

- **新增 `sessions` 表**：MySQL 中新建会话元信息表，用于侧边栏列表展示（title 截取首条消息前 20 字）
- **读写分离（Dual-Write）**：用户消息同步写入 Redis（ChatManager，用于 RAG 上下文），异步写入 MySQL（持久化）
- **会话创建时机**：用户发送第一条消息时自动创建会话，无需手动点按钮
- **会话-知识库绑定**：一个会话绑定一个知识库，`kb_id` 为空字符串 `''` 代表"所有知识库"模式
- **移除 FK 约束**：`conversation_history.kb_id` 不再引用 `knowledge_base` 表，改为无约束的字符串字段
- **新增 API 接口**：会话列表 / 单会话消息 / 删除会话
- **前端侧边栏改为会话历史列表**：显示会话标题、最后活跃时间，支持点击切换、删除会话
- **SSE 流结束后异步写入 conversation_history 表**：确保 RAG 回答完整后持久化到 MySQL

## Capabilities

### New Capabilities
- `session-management`: 会话的 CRUD 操作，包括列表查询、单会话消息加载、删除会话。支持会话与知识库的关联查询和筛选
- `session-sidebar`: 前端侧边栏显示会话历史列表，支持新建会话、切换会话、删除会话、显示最后活跃时间

### Modified Capabilities

<!-- 无现有 specs 需要修改 -->

## Impact

- `src/config/queries.py` — 新增 `sessions` 表建表语句和 CRUD SQL
- `src/mysql_db.py` — 新增 `sessions` 表的操作方法（create / list / delete / update_title），修改 `conversation_history` 表创建语句（移除 FK）
- `src/chat_manager.py` — 新增异步写入 MySQL 的接口，session 枚举能力
- `src/api/routes/chat.py` — SSE 流结束后写入 conversation_history 表
- `src/api/routes/` — 新增 `sessions.py` 路由文件（GET /api/sessions, GET /api/sessions/{id}/messages, DELETE /api/sessions/{id}）
- `nginx/html/chat.html` — 侧边栏从知识库列表改为会话历史列表
- `nginx/html/js/chat.js` — 新增会话管理逻辑（创建/切换/删除/加载消息）
- `nginx/html/js/api.js` — 新增会话相关 API 调用
- `nginx/html/css/style.css` — 侧边栏样式适配
```

## openspec/changes/session-history/design.md

- Source: openspec/changes/session-history/design.md
- Lines: 1-89
- SHA256: 4e457e2caa83fa11c34af8e352c8bdcee3d16a371794613748813d0786925fcc

[TRUNCATED]

```md
## Context

当前 Financial QA MVP 的聊天页面（`nginx/html/chat.html`）左侧侧边栏展示的是知识库列表，不支持会话历史管理。对话存储在 Redis（`chat_manager.py`）中，以 `chat_history:{session_id}` 为 key 的 List 结构存储，无持久化能力。MySQL 的 `conversation_history` 表已存在但代码中完全未被使用。

用户需要像主流 AI 聊天应用一样的会话管理体验：查看历史会话、切换对话、回溯之前的问答内容。

## Goals / Non-Goals

**Goals:**

- 用户发送第一条消息时自动创建会话，标题截取首条消息前 20 字
- 侧边栏从知识库列表改为会话历史列表（显示标题 + 最后活跃时间）
- 点击侧边栏会话可切换加载该会话的完整消息历史
- 支持删除会话
- 消息写入采用双写模式：同步 Redis（保证 RAG 上下文不中断）+ 异步 MySQL（持久化）
- 每个会话绑定一个知识库（`kb_id` 为空字符串代表"所有知识库"模式）
- 移除 `conversation_history.kb_id` 的 FK 约束
- 新建 `sessions` 表存储会话元信息

**Non-Goals:**

- 消息编辑或撤回
- 会话重命名（用户手动编辑标题）
- 会话搜索或全文检索
- 多用户/多租户支持
- 会话分享或导出
- 知识库管理功能移出侧边栏（知识库管理仍有独立页面）

## Decisions

### D1: 双写模式 — Redis 同步 + MySQL 异步

- **选择**：用户消息同步写入 Redis（ChatManager），RAG 回答完成后异步写入 MySQL
- **理由**：Redis 写入在 1ms 级别，保证 RAG 链路的 `get_window()` 不受影响；MySQL 写入可接受几十毫秒延迟，放在 SSE 流结束后执行，不阻塞用户
- **替代方案**：只写 MySQL — 但 `get_window()` 每次都要查 MySQL，延迟增加 5-10ms；只写 Redis — 会话不持久化，服务重启后丢失

### D2: 新建 `sessions` 表

- **选择**：MySQL 中新建独立 `sessions` 表存储会话元信息，而非复用现有表或依赖 Redis
- **理由**：侧边栏需要列出"所有会话"并按最后活跃时间排序，Redis 的 List 不支持此查询模式
- **替代方案**：在 `conversation_history` 上做 `SELECT DISTINCT session_id` — 数据量大时性能差，且无法存储标题和最后活跃时间等元信息

### D3: 会话标题 = 首条用户消息截取 20 字

- **选择**：自动从首条用户消息中截取前 20 字作为标题
- **理由**：零用户操作成本，主流方案（ChatGPT、Claude）均采用此策略
- **替代方案**：让用户手动输入标题 — 增加交互摩擦；用 LLM 生成标题 — 额外开销，MVP 不必要

### D4: 会话在第一条消息时创建

- **选择**：发送第一条消息时自动创建 `sessions` 记录，而非点击"新建会话"按钮时创建
- **理由**：避免产生空会话（用户点了按钮但没发消息），减少数据库垃圾数据
- **替代方案**：点击按钮创建 — 会产生空会话记录，需要额外清理逻辑

### D5: 切换会话时从 MySQL 加载消息

- **选择**：从 MySQL `conversation_history` 表加载完整消息历史
- **理由**：Redis 只保留当前会话的最近 N 条消息（RAG 上下文窗口），切换会话时历史消息已不在 Redis 中
- **替代方案**：Redis 缓存所有会话消息 — 内存不可控，不是 Redis 的设计用途

### D6: kb_id = '' 代表"所有知识库"

- **选择**：`kb_id` 使用空字符串 `''` 表示查询所有知识库，而非 NULL 或虚拟 KB 记录
- **理由**：NULL 在 SQL 中语义模糊（`IS NULL` 与普通查询不一致），且业务代码需要大量判空逻辑；虚拟 KB 记录污染数据库、增加多余业务代码。DEFAULT `''` 即可
- **FK 约束**：移除 `conversation_history.kb_id` 的 FK 约束，改为无约束的 VARCHAR(36)。`sessions` 表的 `kb_id` 同样无 FK

### D7: session_id 生成策略

- **选择**：沿用前端 `generateSessionId()` 的 `session_<timestamp>_<random>` 格式
- **理由**：避免前后端 session_id 生成逻辑不一致，现有格式已在 SSE 中使用，无需改动

### D8: SSE 流结束时异步写入 MySQL

- **选择**：在 `/api/chat` SSE 流结束后，将完整的 user message + assistant response 异步写入 MySQL
- **理由**：SSE 流持续 10-30 秒，如果同步写入会阻塞用户体验；异步写入用 `asyncio.create_task` 或后台线程执行，不阻塞 SSE 响应
- **细节**：`sessions` 表在用户发送第一条消息时就创建（获取到标题），`conversation_history` 表在每轮对话结束时写入

## Risks / Trade-offs

| 风险 | 缓解措施 |
```

Full source: openspec/changes/session-history/design.md

## openspec/changes/session-history/tasks.md

- Source: openspec/changes/session-history/tasks.md
- Lines: 1-58
- SHA256: 690e5eb0cc29d51d77dc18a9e5f5aad4e8f27675685fc8e7a80de5140472bcb4

```md
## 1. 数据库层变更

- [ ] 1.1 在 `src/config/queries.py` 中新增 `CREATE_TABLE_SESSIONS` 建表语句（id, title, kb_id, created_at, updated_at）
- [ ] 1.2 修改 `CREATE_TABLE_CONVERSATION_HISTORY`：移除 `kb_id` 的 FOREIGN KEY 约束，保留列定义但不引用 knowledge_base 表
- [ ] 1.3 在 `src/config/queries.py` 中新增 sessions 表的 CRUD SQL：INSERT_SESSION, SELECT_SESSIONS, SELECT_SESSION_BY_ID, SELECT_MESSAGES_BY_SESSION, DELETE_SESSION, DELETE_MESSAGES_BY_SESSION, UPDATE_SESSION_TITLE
- [ ] 1.4 在 `src/mysql_db.py` 中新增 sessions 表的操作方法：`create_session()`, `get_sessions()`, `get_session_by_id()`, `get_messages()`, `delete_session()`, `update_session_title()`
- [ ] 1.5 在 `src/mysql_db.py` 中新增 `save_message()` 方法用于写入 conversation_history
- [ ] 1.6 更新 `init_db()` 调用 CREATE_TABLE_SESSIONS

## 2. 后端 API

- [ ] 2.1 创建 `src/api/routes/sessions.py` 路由文件，实现三个端点：
  - `GET /api/sessions` — 返回最近 50 条会话列表（含 kb_name、message_count）
  - `GET /api/sessions/{session_id}/messages` — 返回会话消息历史（按时间正序）
  - `DELETE /api/sessions/{session_id}` — 删除会话及其所有消息
- [ ] 2.2 在 `src/api/main.py` 中注册 sessions 路由

## 3. ChatManager 双写改造

- [ ] 3.1 在 `src/chat_manager.py` 中新增 `set_mysql_db()` 注入方法（接收 MySQLDB 实例）
- [ ] 3.2 在 `src/chat_manager.py` 中新增 `save_session_async()` 方法：首次消息时创建 sessions 记录并更新标题
- [ ] 3.3 在 `src/chat_manager.py` 中新增 `save_messages_async()` 方法：将 user + assistant 消息异步写入 conversation_history
- [ ] 3.4 在 `src/chat_manager.py` 中新增 `delete_session_cleanup()` 方法：删除 Redis key

## 4. SSE 端点改造

- [ ] 4.1 修改 `src/api/routes/chat.py` 的流式响应逻辑：在 SSE 流结束后，异步调用 `save_session_async()` 和 `save_messages_async()`
- [ ] 4.2 确保 SSE 第一个事件携带 `session_id` 字段，供前端识别新创建的会话

## 5. 前端 API 层

- [ ] 5.1 在 `nginx/html/js/api.js` 中新增 API 调用：
  - `fetchSessions()` — GET /api/sessions
  - `fetchSessionMessages(sessionId)` — GET /api/sessions/{sessionId}/messages
  - `deleteSession(sessionId)` — DELETE /api/sessions/{sessionId}

## 6. 前端侧边栏改造

- [ ] 6.1 修改 `nginx/html/chat.html`：侧边栏从知识库列表改为会话历史列表，包含"新建会话"按钮
- [ ] 6.2 修改 `nginx/html/js/chat.js`：新增会话管理逻辑
  - 页面加载时调用 `fetchSessions()` 填充侧边栏
  - 点击会话切换，加载消息历史
  - 新建会话逻辑（生成 session_id、清空聊天区）
  - 删除会话逻辑（确认对话框 → 调用 API → 刷新列表）
  - 消息发送后刷新侧边栏（新会话出现、已有会话移到顶部）
  - 加载/空/错误状态处理
- [ ] 6.3 修改 `nginx/html/js/chat.js`：会话切换时同步更新 KB 选择器
- [ ] 6.4 修改 `nginx/html/css/style.css`：侧边栏样式适配（会话列表、hover 效果、删除按钮）

## 7. 集成与验证

- [ ] 7.1 重启 Docker 服务，验证数据库表创建成功
- [ ] 7.2 发送消息验证：会话自动创建、标题正确截取前 20 字
- [ ] 7.3 验证会话切换：点击侧边栏会话，加载并显示历史消息
- [ ] 7.4 验证删除会话：删除后侧边栏移除、消息清空
- [ ] 7.5 验证"所有知识库"模式（kb_id = ''）下会话创建和切换正常
- [ ] 7.6 验证冷启动：重启容器后 Redis 无数据，MySQL 中会话列表和消息正常加载
- [ ] 7.7 Ruff 格式检查和测试通过
```

## openspec/changes/session-history/specs/session-management/spec.md

- Source: openspec/changes/session-history/specs/session-management/spec.md
- Lines: 1-79
- SHA256: 59bb5108f3ea7cf8002106bd08565f27b76c0a96c1f1bb31ed9d8768ff2ad79f

```md
## ADDED Requirements

### Requirement: System SHALL create session on first user message

When a user sends the first message in a new session, the system SHALL automatically create a session record in MySQL.

- Session title SHALL be the first 20 characters of the user's first message (ellipsis optional, no `...` appended)
- Session ID SHALL be generated by the frontend using `session_<timestamp>_<random>` format
- Session SHALL be associated with the currently selected `kb_id` (empty string `''` for "all KBs" mode)
- `created_at` and `updated_at` SHALL be set to current timestamp

#### Scenario: Create session on first message
- **WHEN** user sends a message in a new session
- **THEN** system creates a record in `sessions` table with title = first 20 chars of message
- **AND** system returns `session_id` in SSE response headers or first event

### Requirement: System SHALL list sessions ordered by last activity

The system SHALL provide a `GET /api/sessions` endpoint that returns recent sessions, ordered by `updated_at DESC`.

- Response SHALL include: `id`, `title`, `kb_id`, `kb_name`, `message_count`, `created_at`, `updated_at`
- Result SHALL be limited to most recent 50 sessions
- Each session SHALL include the associated knowledge base name (empty kb_id → `kb_name = "所有知识库"`)

#### Scenario: List sessions returns recent sessions
- **WHEN** client calls `GET /api/sessions`
- **THEN** response SHALL contain array of session objects sorted by `updated_at` descending
- **AND** each session SHALL include `id`, `title`, `kb_id`, `kb_name`, `message_count`, `created_at`, `updated_at`

#### Scenario: Empty session list returns empty array
- **WHEN** client calls `GET /api/sessions` and no sessions exist
- **THEN** response SHALL be `[]`

### Requirement: System SHALL load full message history for a session

The system SHALL provide a `GET /api/sessions/{session_id}/messages` endpoint that returns all messages for a session.

- Response SHALL include: `role`, `content`, `sources`, `created_at`
- Messages SHALL be ordered by `created_at ASC`
- `sources` SHALL be a JSON array of citation strings (may be null)

#### Scenario: Load messages for existing session
- **WHEN** client calls `GET /api/sessions/{session_id}/messages`
- **THEN** response SHALL contain array of message objects ordered by `created_at` ascending
- **AND** each message SHALL have `role`, `content`, `sources`, `created_at`

#### Scenario: Load messages for non-existing session returns 404
- **WHEN** client calls `GET /api/sessions/{session_id}/messages` with unknown `session_id`
- **THEN** response SHALL be `{"detail": "Session not found"}` with status 404

### Requirement: System SHALL delete a session

The system SHALL provide a `DELETE /api/sessions/{session_id}` endpoint that removes a session and its messages.

- Deleting a session SHALL cascade-delete all associated messages in `conversation_history`
- Deleting a session SHALL NOT affect ChromaDB vector data or knowledge bases
- Redis keys for the deleted session SHALL be deleted (best-effort)

#### Scenario: Delete existing session
- **WHEN** client calls `DELETE /api/sessions/{session_id}`
- **THEN** system deletes the session record and all associated messages
- **AND** returns `{"success": true}`

#### Scenario: Delete non-existing session returns 404
- **WHEN** client calls `DELETE /api/sessions/{session_id}` with unknown `session_id`
- **THEN** response SHALL be `{"detail": "Session not found"}` with status 404

### Requirement: System SHALL persist messages to conversation_history after SSE stream

After the SSE streaming response completes, the system SHALL asynchronously write the user's query and the assistant's full response to `conversation_history` table.

- User message SHALL be written with `role = 'user'`, `content` = the query text, `sources` = NULL
- Assistant response SHALL be written with `role = 'assistant'`, `content` = full answer text, `sources` = JSON array of citation strings
- Write SHALL be asynchronous (non-blocking), with up to 3 retry attempts on failure
- On write failure, SHALL log warning but NOT affect the SSE response already sent

#### Scenario: Persist messages after SSE stream
- **WHEN** SSE stream for a chat response completes
- **THEN** system writes user message and assistant response to `conversation_history` table with correct `session_id` and `kb_id`
```

## openspec/changes/session-history/specs/session-sidebar/spec.md

- Source: openspec/changes/session-history/specs/session-sidebar/spec.md
- Lines: 1-103
- SHA256: ce85498f1c45725d231a814f999e7f59c1d56b47d6bf69b94e9262be9dc14409

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Sidebar SHALL display session history list

The chat page left sidebar SHALL display a list of conversation sessions instead of the current knowledge base list.

- Each session item SHALL show: session title (first 20 chars of first message), last activity time (relative, e.g. "3分钟前"), knowledge base badge
- Sessions SHALL be ordered by last activity time, most recent first
- Maximum 50 sessions shown in the list
- Empty state: when no sessions exist, SHALL display a placeholder text "暂无会话"
- Loading state: when sessions are loading, SHALL display a loading indicator
- Error state: when loading fails, SHALL display "加载失败" with a retry button

#### Scenario: Sidebar shows session list on page load
- **WHEN** user opens the chat page
- **THEN** sidebar SHALL call `GET /api/sessions` to load session list
- **AND** sidebar SHALL display session items with title and last activity time

#### Scenario: Empty session list shows placeholder
- **WHEN** user opens the chat page and no sessions exist
- **THEN** sidebar SHALL display "暂无会话" placeholder

#### Scenario: Loading state shows spinner
- **WHEN** user opens the chat page and sessions are being fetched
- **THEN** sidebar SHALL show a loading spinner

#### Scenario: Error state shows retry button
- **WHEN** session list loading fails
- **THEN** sidebar SHALL display "加载失败" with a retry button
- **AND** clicking retry SHALL re-fetch the session list

### Requirement: User SHALL be able to switch between sessions

Clicking a session in the sidebar SHALL switch the chat view to that session's conversation history.

- Clicking a session SHALL call `GET /api/sessions/{id}/messages` to load messages
- The chat area SHALL display all loaded messages
- The active session SHALL be visually highlighted in the sidebar
- The KB selector SHALL update to match the session's `kb_id`
- After switching, new messages SHALL be sent using the switched session's `session_id`

#### Scenario: Switch to a session
- **WHEN** user clicks a session in the sidebar
- **THEN** system loads that session's messages from API
- **AND** chat area displays the message history
- **AND** the clicked session is visually highlighted

#### Scenario: KB selector updates on session switch
- **WHEN** user switches to a session that has a `kb_id`
- **THEN** the KB selector dropdown SHALL update to match the session's `kb_id`
- **AND** if `kb_id` is empty string, selector SHALL show "所有知识库"

### Requirement: User SHALL be able to create a new session

The sidebar SHALL have a "新建会话" button at the top.

- Clicking "新建会话" SHALL clear the current chat area
- A new `session_id` SHALL be generated by the frontend
- The session SHALL be saved to `sessions` table only after the user sends the first message
- The new session SHALL use the currently selected KB in the dropdown

#### Scenario: Create new session
- **WHEN** user clicks "新建会话" button
- **THEN** chat area clears
- **AND** a new `session_id` is generated
- **AND** the sidebar does NOT show this session (it will appear after first message)

### Requirement: User SHALL be able to delete a session

Each session item SHALL have a delete button (appears on hover).

- Clicking delete SHALL show a confirmation dialog "确认删除此会话？"
- Confirming SHALL call `DELETE /api/sessions/{id}`
- After deletion, the session SHALL be removed from the sidebar
- If the deleted session was the active session, SHALL create/switch to a new session

#### Scenario: Delete session with confirmation
- **WHEN** user hovers over a session item
- **THEN** a delete button appears
- **AND** clicking the delete button SHALL show a confirmation dialog
```

Full source: openspec/changes/session-history/specs/session-sidebar/spec.md

