---
comet_change: session-history
role: technical-design
canonical_spec: openspec
---

# Session History — 技术设计文档

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      前端 (chat.html)                        │
│  ┌──────────────┐  ┌──────────────────────────────────┐     │
│  │  侧边栏       │  │  聊天区                           │     │
│  │  ├ 会话列表   │  │  ├ 消息历史渲染                   │     │
│  │  ├ 新建按钮   │  │  ├ SSE 流式接收 token             │     │
│  │  ├ 悬浮删除   │  │  └ 来源引用展示                   │     │
│  │  └ 会话高亮   │  │                                   │     │
│  └──────────────┘  └──────────────────────────────────┘     │
└──────────┬────────────────────┬──────────────────────────────┘
           │ fetchSessions()    │ EventSource /api/chat/stream
           │ fetchMessages()    │ + fetchSessions() refresh
           ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI 后端                              │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐    │
│  │ sessions.py  │  │ chat.py      │  │ knowledge_base │    │
│  │  新路由文件   │  │  SSE 流式    │  │ .py           │    │
│  └──────┬───────┘  └──────┬───────┘  └───────┬────────┘    │
│         │                 │                   │             │
│         ▼                 ▼                   ▼             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              AppService + MySQLDB                    │  │
│  │  sessions表   conversation_history  knowledge_base   │  │
│  │                (FK removed)         document         │  │
│  └──────────────────────────────────────────────────────┘  │
│         │                 │                                 │
│         ▼                 ▼                                 │
│  ┌─────────────┐  ┌──────────────┐                         │
│  │   Redis      │  │  ChromaDB    │                         │
│  │ ChatManager  │  │  向量存储    │                         │
│  │ (sync write) │  │             │                         │
│  └─────────────┘  └──────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

### 关键设计原则

- **冷热分离**：Redis 存热数据（当前会话 RAG 上下文），MySQL 存冷数据（完整持久化）
- **双写不同步**：Redis 同步、MySQL 异步；两者角色不同，短期不一致可接受
- **无 FK 约束**：kb_id 用空字符串代表"所有知识库"，不再引用 knowledge_base 表

## 2. 数据库设计

### 2.1 新表 sessions

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    title       VARCHAR(20)  NOT NULL DEFAULT '',
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_updated_at (updated_at DESC)
);
```

字段说明：
- `id` — 沿用前端 `session_<timestamp>_<random>` 格式
- `title` — 首条用户消息截取前 20 字，首次消息时写入；后续不更新
- `kb_id` — 关联的知识库 ID，空字符串代表"所有知识库"；无 FK 约束
- `created_at` — 创建时间
- `updated_at` — 最后活跃时间，侧边栏按此字段倒序排列

### 2.2 conversation_history 表修改

移除原有 FK 约束，保留列定义：

```sql
CREATE TABLE IF NOT EXISTS conversation_history (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(36)  NOT NULL,
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    role        ENUM('user','assistant') NOT NULL,
    content     TEXT         NOT NULL,
    sources     JSON,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at)
);
```

变更：
- `kb_id` 保持 NOT NULL DEFAULT ''，移除 FOREIGN KEY 引用
- 其他字段不变

### 2.3 会话列表查询

```sql
SELECT s.id, s.title, s.kb_id, s.created_at, s.updated_at,
       COALESCE(kb.name, '所有知识库') AS kb_name,
       COUNT(ch.id) AS message_count
FROM sessions s
LEFT JOIN knowledge_base kb ON s.kb_id = kb.id AND s.kb_id != ''
LEFT JOIN conversation_history ch ON ch.session_id = s.id
GROUP BY s.id
ORDER BY s.updated_at DESC
LIMIT 50
```

## 3. API 设计

### 3.1 GET /api/sessions — 会话列表

**Response:**
```json
[
  {
    "id": "session_1719465600_a1b2c3",
    "title": "厦门公司财务指标分析",
    "kb_id": "550e8400...",
    "kb_name": "2026年年报",
    "message_count": 12,
    "created_at": "2026-06-27T10:00:00",
    "updated_at": "2026-06-27T10:30:00"
  }
]
```

**错误：** 始终返回 200 + 数组，无会话时返回 `[]`

### 3.2 GET /api/sessions/{session_id}/messages — 消息历史

**Response:**
```json
[
  {
    "role": "user",
    "content": "厦门公司的财务指标",
    "sources": null,
    "created_at": "2026-06-27T10:00:00"
  },
  {
    "role": "assistant",
    "content": "根据文档...",
    "sources": ["2026年报.pdf (第5页)"],
    "created_at": "2026-06-27T10:00:05"
  }
]
```

**错误：** session_id 不存在时返回 404 `{"detail": "Session not found"}`

### 3.3 DELETE /api/sessions/{session_id} — 删除会话

**Response:**
```json
{"success": true}
```

**错误：** session_id 不存在时返回 404

**级联删除：**
1. 删除 `sessions` 记录
2. 删除 `conversation_history` 中所有该 session 的消息
3. 尝试删除 Redis key `chat_history:{session_id}`（尽力而为）

## 4. 后端实现细节

### 4.1 src/config/queries.py — 新增 SQL

```python
CREATE_TABLE_SESSIONS: str = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    title       VARCHAR(20)  NOT NULL DEFAULT '',
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_updated_at (updated_at DESC)
)
"""

SELECT_SESSIONS: str = """\
SELECT s.id, s.title, s.kb_id, s.created_at, s.updated_at,
       COALESCE(kb.name, '所有知识库') AS kb_name,
       COUNT(ch.id) AS message_count
FROM sessions s
LEFT JOIN knowledge_base kb ON s.kb_id = kb.id AND s.kb_id != ''
LEFT JOIN conversation_history ch ON ch.session_id = s.id
GROUP BY s.id
ORDER BY s.updated_at DESC
LIMIT 50
"""

SELECT_MESSAGES_BY_SESSION: str = """\
SELECT role, content, sources, created_at
FROM conversation_history
WHERE session_id = %s
ORDER BY created_at ASC
"""

INSERT_SESSION: str = """\
INSERT INTO sessions (id, title, kb_id) VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
"""

DELETE_SESSION: str = """\
DELETE FROM sessions WHERE id = %s
"""

DELETE_MESSAGES_BY_SESSION: str = """\
DELETE FROM conversation_history WHERE session_id = %s
"""

INSERT_MESSAGE: str = """\
INSERT INTO conversation_history (session_id, kb_id, role, content, sources)
VALUES (%s, %s, %s, %s, %s)
"""
```

**注意**：`INSERT_SESSION` 使用 `ON DUPLICATE KEY UPDATE` 而非 `INSERT IGNORE`，是因为首次消息后 SSE 返回前可能有重试场景，需要确保 updated_at 更新而 title 不变。

### 4.2 src/mysql_db.py — 新增方法

```python
def create_session(self, session_id, title, kb_id):
    """创建或更新会话记录。"""

def get_sessions(self):
    """返回最近 50 条会话列表。"""

def get_messages(self, session_id):
    """返回会话的消息历史。"""

def delete_session_and_messages(self, session_id):
    """删除会话及其所有消息。"""

def save_message(self, session_id, kb_id, role, content, sources=None):
    """保存单条消息到 conversation_history。"""
```

`get_sessions()` 和 `get_messages()` 使用 `self._lock` 保护线程安全。所有写操作在事务内执行。

### 4.3 src/chat_manager.py — 双写改造

新增 MySQL 注入和异步方法：

```python
class ChatManager:
    def set_mysql_db(self, mysql_db: MySQLDB) -> None:
        """注入 MySQLDB 实例用于异步持久化。"""
    
    async def save_session_async(self, session_id: str, title: str, kb_id: str) -> None:
        """异步创建会话记录。首次消息时调用。
        使用 asyncio.to_thread 将同步 MySQL 调用放到线程池。"""
    
    async def save_messages_async(self, session_id: str, kb_id: str,
                                   user_msg: str, assistant_msg: str,
                                   sources: list[str]) -> None:
        """异步写入 user + assistant 消息。
        两次 INSERT 在同一事务内保证原子性。"""
    
    def cleanup_session(self, session_id: str) -> None:
        """删除 Redis 中的会话 key（尽力而为）。"""
```

**异步实现方案**：
```python
async def save_session_async(self, session_id, title, kb_id):
    if self._mysql_db is None:
        return
    try:
        await asyncio.to_thread(
            self._mysql_db.create_session, session_id, title, kb_id
        )
    except Exception as e:
        logger.warning("Failed to save session async: {}", e)

async def save_messages_async(self, session_id, kb_id, user_msg, assistant_msg, sources):
    if self._mysql_db is None:
        return
    try:
        await asyncio.to_thread(
            self._mysql_db.save_message, session_id, kb_id, 'user', user_msg, None
        )
        await asyncio.to_thread(
            self._mysql_db.save_message, session_id, kb_id, 'assistant', assistant_msg, sources
        )
    except Exception as e:
        logger.warning("Failed to save messages async: {}", e)
```

### 4.4 src/api/routes/chat.py — SSE 端点改造

在 `_stream_rag_response` 的 `done` 事件发送后，添加异步持久化：

```python
async def _stream_rag_response(kb_id, session_id, query):
    # ... 现有流式逻辑 ...
    
    # 流结束后异步持久化
    # 使用 create_task 非阻塞执行，不等完成
    svc = _get_service()
    asyncio.create_task(
        _persist_conversation(svc, session_id, kb_id, query, full_answer, sources)
    )
    
    yield "event: done\ndata: {}\n\n"


async def _persist_conversation(svc, session_id, kb_id, query, answer, sources):
    """异步持久化对话到 MySQL，带重试。
    不抛出异常——失败只记日志，不影响已返回的 SSE 响应。"""
    svc.rag_chain.chat_manager.set_mysql_db(svc.db)
    
    # 创建会话（如首次消息）
    title = query[:20]
    async def retry(coro, max_retries=3):
        for i in range(max_retries):
            try:
                await coro
                return
            except Exception as e:
                if i < max_retries - 1:
                    await asyncio.sleep(0.5 * (i + 1))
                else:
                    logger.warning("Persist failed after {} retries: {}", max_retries, e)
    
    await retry(
        svc.rag_chain.chat_manager.save_session_async(session_id, title, kb_id)
    )
    await retry(
        svc.rag_chain.chat_manager.save_messages_async(
            session_id, kb_id, query, answer, sources
        )
    )
```

### 4.5 src/api/routes/sessions.py — 新路由文件

```python
router = APIRouter()

@router.get("/sessions")
async def list_sessions():
    """列出最近 50 个会话。"""
    svc = _get_service()
    sessions = svc.db.get_sessions()
    return sessions  # list[dict]

@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """获取会话消息历史。"""
    svc = _get_service()
    # 先检查 session 是否存在
    session = svc.db.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = svc.db.get_messages(session_id)
    return messages

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其消息。"""
    svc = _get_service()
    # 清理 Redis
    svc.rag_chain.chat_manager.cleanup_session(session_id)
    # 删除 MySQL 记录
    ok = svc.db.delete_session_and_messages(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True}
```

## 5. 前端实现

### 5.1 侧边栏改造 (chat.html)

将现有知识库列表替换为会话列表结构：

```html
<div class="flex-1 overflow-hidden flex flex-col">
    <div class="px-4 py-3 flex items-center justify-between border-b border-slate-700">
        <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">会话历史</span>
        <button onclick="newSession()" class="w-6 h-6 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 flex items-center justify-center text-sm">
            <svg>+</svg>
        </button>
    </div>
    <div class="sidebar-session-list flex-1 overflow-y-auto px-3 pb-2"></div>
</div>
```

### 5.2 前端状态管理 (chat.js)

```javascript
// 全局状态
let currentSessionId = generateSessionId();
let sessions = [];  // 缓存会话列表

// 页面加载：获取会话列表
async function loadSessions() {
    try {
        sessions = await fetchSessions();
        renderSidebar();
    } catch (err) {
        showError('加载会话失败');
    }
}

// 切换会话
async function switchSession(sessionId) {
    // 保存当前会话（如果还在流式，先 abort）
    if (abortController) abortController.abort();
    
    // 加载消息历史
    const messages = await fetchSessionMessages(sessionId);
    currentSessionId = sessionId;
    renderMessages(messages);
    
    // 更新 KB 选择器
    const session = sessions.find(s => s.id === sessionId);
    document.getElementById('kb-select').value = session.kb_id || '';
    
    // 高亮当前会话
    renderSidebar(sessionId);
}

// 新建会话
function newSession() {
    if (abortController) abortController.abort();
    currentSessionId = generateSessionId();
    clearChatArea();
    renderSidebar(null);
}

// 删除会话
async function deleteSession(sessionId) {
    if (!confirm('确认删除此会话？')) return;
    await deleteSessionAPI(sessionId);
    if (sessionId === currentSessionId) {
        newSession();
    }
    await loadSessions();
}

// 消息发送后刷新侧边栏
function sendMessage() {
    // ... 现有 SSE 逻辑 ...
    // done 事件中加入：
    evtSource.addEventListener('done', () => {
        // ... 现有处理 ...
        loadSessions();  // 刷新侧边栏
    });
}
```

### 5.3 API 新增 (api.js)

```javascript
async function fetchSessions() {
    return apiRequest('/sessions');
}

async function fetchSessionMessages(sessionId) {
    return apiRequest(`/sessions/${sessionId}/messages`);
}

async function deleteSessionAPI(sessionId) {
    return apiRequest(`/sessions/${sessionId}`, { method: 'DELETE' });
}
```

## 6. 错误处理与边界条件

### 6.1 异步写入失败

```
MySQL 写入失败（网络/连接/超时）
  → 重试 3 次，间隔 0.5s → 1s → 1.5s
  → 全部失败 → logger.warning，不抛异常
  → 不影响已返回的 SSE 流式响应
  → Redis 仍有最近会话数据，服务重启后 Redis 丢失但 MySQL 可能部分缺失
```

### 6.2 会话删除原子性

```
DELETE /api/sessions/{id}
  1. 删除 Redis key（尽力而为，失败只记日志）
  2. 删除 MySQL sessions 记录（事务内）
  3. 级联删除 conversation_history 消息（同一事务）
  → 事务失败 → 什么都不删，数据一致
  → 如果 Redis 删了但 MySQL 事务回滚 → Redis 少一点数据，下次写会重建
```

### 6.3 冷启动场景

```
Docker 重启后：
  - Redis：所有会话数据消失
  - MySQL：sessions 表和 conversation_history 表完整
  - 用户访问聊天页 → 侧边栏从 MySQL 加载会话列表
  - 切换会话 → 从 MySQL 加载消息历史
  - 发送新消息 → Redis 重新开始累积
```

### 6.4 并发安全

```
MySQLDB 使用 threading.RLock 保护所有操作
  - create_session/get_sessions 互斥
  - 前端多个 tab 同时操作同一 session → 数据库行级锁保证不冲突
  - INSERT_SESSION 使用 ON DUPLICATE KEY UPDATE 保证幂等
```

## 7. 测试策略

| 测试场景 | 方法 |
|---------|------|
| 会话列表返回正确 JSON 结构 | pytest + mock MySQLDB.get_sessions() |
| 空会话列表返回 [] | 同上，mock 返回空列表 |
| 消息历史按 created_at ASC 排序 | 插入乱序数据，验证返回顺序 |
| 不存在的 session_id 返回 404 | 直接测试路由 |
| 删除会话同时清理消息 | 事务内验证两条 DELETE 在同一连接执行 |
| 异步写入失败不抛异常 | mock MySQLDB.save_message 抛异常，验证 logger.warning 被调用 |
| SSE 流结束后异步写入被调用 | mock asyncio.create_task，验证调用次数 |
| 前端切换会话更新 KB 选择器 | 设置 session.kb_id=''，验证 select value 为空字符串 |

## 8. 未纳入 MVP 的特性

- 会话重命名（手动编辑标题）
- 会话搜索（全文检索历史消息）
- 分页加载更多历史会话
- 消息编辑或撤回
- 文件附件在会话中显示
