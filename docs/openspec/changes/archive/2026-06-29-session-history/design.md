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
|------|---------|
| MySQL 异步写入失败导致会话丢失 | 异步任务带重试（3 次），且 Redis 中仍有最近会话的消息 |
| dual-write 导致 Redis 和 MySQL 数据不一致 | Redis 是"热数据"（当前会话用），MySQL 是"冷数据"（历史查询用），两者角色不同，短期不一致可接受 |
| 会话数量增长后侧边栏加载慢 | 限制列表返回最近 50 条，加 `LIMIT + OFFSET` 分页 |
| 删除会话时清理不完整 | 级联删除：删 sessions 记录 → 删 conversation_history 记录 → 删 Redis key（尽力而为） |

## Open Questions

- 删除会话后是否保留 ChromaDB 中的向量数据？当前决策：**保留**，向量数据属于知识库，不属于会话
