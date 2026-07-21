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
