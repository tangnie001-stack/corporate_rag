## 1. 数据库层变更

- [x] 1.1 在 `src/config/queries.py` 中新增 `CREATE_TABLE_SESSIONS` 建表语句（id, title, kb_id, created_at, updated_at）
- [x] 1.2 修改 `CREATE_TABLE_CONVERSATION_HISTORY`：移除 `kb_id` 的 FOREIGN KEY 约束，保留列定义但不引用 knowledge_base 表
- [x] 1.3 在 `src/config/queries.py` 中新增 sessions 表的 CRUD SQL：INSERT_SESSION, SELECT_SESSIONS, SELECT_SESSION_BY_ID, SELECT_MESSAGES_BY_SESSION, DELETE_SESSION, DELETE_MESSAGES_BY_SESSION, UPDATE_SESSION_TITLE
- [x] 1.4 在 `src/mysql_db.py` 中新增 sessions 表的操作方法：`create_session()`, `get_sessions()`, `get_session_by_id()`, `get_messages()`, `delete_session()`, `update_session_title()`
- [x] 1.5 在 `src/mysql_db.py` 中新增 `save_message()` 方法用于写入 conversation_history
- [x] 1.6 更新 `init_db()` 调用 CREATE_TABLE_SESSIONS

## 2. 后端 API

- [x] 2.1 创建 `src/api/routes/sessions.py` 路由文件，实现三个端点：
  - `GET /api/sessions` — 返回最近 50 条会话列表（含 kb_name、message_count）
  - `GET /api/sessions/{session_id}/messages` — 返回会话消息历史（按时间正序）
  - `DELETE /api/sessions/{session_id}` — 删除会话及其所有消息
- [x] 2.2 在 `src/api/main.py` 中注册 sessions 路由

## 3. ChatManager 双写改造

- [x] 3.1 在 `src/chat_manager.py` 中新增 `set_mysql_db()` 注入方法（接收 MySQLDB 实例）
- [x] 3.2 在 `src/chat_manager.py` 中新增 `save_session_async()` 方法：首次消息时创建 sessions 记录并更新标题
- [x] 3.3 在 `src/chat_manager.py` 中新增 `save_messages_async()` 方法：将 user + assistant 消息异步写入 conversation_history
- [x] 3.4 在 `src/chat_manager.py` 中新增 `delete_session_cleanup()` 方法：删除 Redis key

## 4. SSE 端点改造

- [x] 4.1 修改 `src/api/routes/chat.py` 的流式响应逻辑：在 SSE 流结束后，异步调用 `save_session_async()` 和 `save_messages_async()`
- [x] 4.2 确保 SSE 第一个事件携带 `session_id` 字段，供前端识别新创建的会话

## 5. 前端 API 层

- [x] 5.1 在 `nginx/html/js/api.js` 中新增 API 调用：
  - `fetchSessions()` — GET /api/sessions
  - `fetchSessionMessages(sessionId)` — GET /api/sessions/{sessionId}/messages
  - `deleteSession(sessionId)` — DELETE /api/sessions/{sessionId}

## 6. 前端侧边栏改造

- [x] 6.1 修改 `nginx/html/chat.html`：侧边栏从知识库列表改为会话历史列表，包含"新建会话"按钮
- [x] 6.2 修改 `nginx/html/js/chat.js`：新增会话管理逻辑
  - 页面加载时调用 `fetchSessions()` 填充侧边栏
  - 点击会话切换，加载消息历史
  - 新建会话逻辑（生成 session_id、清空聊天区）
  - 删除会话逻辑（确认对话框 → 调用 API → 刷新列表）
  - 消息发送后刷新侧边栏（新会话出现、已有会话移到顶部）
  - 加载/空/错误状态处理
- [x] 6.3 修改 `nginx/html/js/chat.js`：会话切换时同步更新 KB 选择器
- [x] 6.4 修改 `nginx/html/css/style.css`：侧边栏样式适配（会话列表、hover 效果、删除按钮）

## 7. 集成与验证

- [x] 7.1 重启 Docker 服务，验证数据库表创建成功
- [x] 7.2 发送消息验证：会话自动创建、标题正确截取前 20 字
- [x] 7.3 验证会话切换：点击侧边栏会话，加载并显示历史消息
- [x] 7.4 验证删除会话：删除后侧边栏移除、消息清空
- [x] 7.5 验证"所有知识库"模式（kb_id = ''）下会话创建和切换正常
- [x] 7.6 验证冷启动：重启容器后 Redis 无数据，MySQL 中会话列表和消息正常加载
- [x] 7.7 Ruff 格式检查和测试通过
