# streaming-decouple Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将流式问答生成与 SSE 连接解耦：刷新/关闭页面不中止生成、只有点「停止」才停、刷新后可续接，并修正消息落库时序（user 请求开始即写）。

**Architecture:** 生成移入后台 asyncio 任务（`StreamingRunManager` 管理），事件带 seq 写入 per-session 缓冲；SSE 订阅者从缓冲读事件推送，断连只退出消费循环。新增 resume/status/cancel 三个接口。单 worker 部署，状态全在进程内。生产者（graph→缓冲）与消费者（缓冲→SSE）结构分离。

**Tech Stack:** Python 3.11+ / FastAPI / asyncio / Redis / MySQL（SQLAlchemy async）/ 原生前端 `deploy/nginx/html/chat.html`

## Global Constraints

- 生产环境单 worker，流式状态在进程内，不做多 worker 假设（CLAUDE.md + docs/agents/defensive-patterns.md）
- 事件名与 payload 格式：实时流与 resume 回放必须完全一致（`event: <type>\ndata: <json>\n\n`）
- `conversation_history.status` 取值 `complete` / `interrupted`
- Redis 历史只存完整轮次；interrupted 部分仅写 MySQL
- user 消息在请求开始同步 await 写入 MySQL（硬前提）
- 不用三元表达式，写完整 if/else；新增常量进 `src/config/`
- 每个函数写 docstring；改动 API 响应结构需同步 `docs/agents/api_contract.md` 与测试断言
- 测试 mock 外部依赖，不发起真实网络调用

## File Structure

**新建：**
- `src/chat/streaming.py` — `StreamingRunManager`（任务注册表 + abort 信号映射 + seq 事件缓冲 + 生命周期）
- `tests/chat/test_streaming.py` — StreamingRunManager 单元测试
- `scripts/migrations/2026-08-31-add-message-status.sql` — status 列迁移存档
- `tests/api/test_streaming_endpoints.py` — resume/status/cancel 接口测试

**修改：**
- `src/infra/db/models/chat.py` + `src/infra/db/mysql_db/models/chat.py` — MessageModel 加 `status` 列
- `src/infra/db/mysql_db/chat_repo.py` — `save_message` 透传 status
- `src/chat/manager.py` — 新增 `save_user_async` / `save_assistant_async`
- `src/chat/persistence.py` — `PersistenceService` 拆分 user/assistant 写入
- `src/api/chat.py` — `GET /api/chat/stream` 改 POST；请求开始落 user；后台任务启动；SSE 订阅
- `src/api/sessions.py` — 新增 `GET /sessions/events`、`GET /sessions/task-status`、`POST /sessions/cancel`
- `src/api/model/response.py` — `MessageItem` 加 `status`
- `src/services/app_service.py` — `get_messages` 透传 status
- `src/services/agent_service.py` — 生产者 coroutine（图事件→seq 事件写缓冲）、`_persist_conversation` 删除
- `src/config/const.py` — `ASK_USER_TIMEOUT_TEXT` 改引导文案
- `deploy/nginx/html/chat.html` — fetch+getReader 传输层、lastSeq、续接、停止按钮

---

# M1 持久化时序调整

## Task 1.1: MessageModel 增加 status 列

**Files:**
- Modify: `src/infra/db/models/chat.py`（`MessageModel`）
- Modify: `src/infra/db/mysql_db/models/chat.py`（同结构副本）
- Test: `tests/infra/db/test_mysql_db.py`

**Interfaces:**
- Produces: `MessageModel.status: str`（默认 `"complete"`）

- [ ] **Step 1: 写失败测试**——`save_message` 后 status 可读回默认值

```python
@pytest.mark.asyncio
async def test_message_status_default_complete():
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.mysql_db import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"sess-status-{uuid.uuid4().hex[:8]}"
    await chat_repo.save_message(
        MessageModel(session_id=session_id, kb_id="", role="user", content="q")
    )
    msgs = await chat_repo.get_messages(session_id)
    assert msgs[0].status == "complete"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/infra/db/test_mysql_db.py::test_message_status_default_complete -v`
Expected: FAIL（`AttributeError: 'MessageModel' object has no attribute 'status'`）

- [ ] **Step 3: 实施**——两个模型文件都加列

`src/infra/db/models/chat.py` 的 `MessageModel` 中 `model_name` 之后加：

```python
    status: Mapped[str] = mapped_column(
        String(16), default="complete", nullable=False, comment="complete/interrupted"
    )
```

`src/infra/db/mysql_db/models/chat.py` 同步同款修改。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/infra/db/test_mysql_db.py::test_message_status_default_complete -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/infra/db/models/chat.py src/infra/db/mysql_db/models/chat.py tests/infra/db/test_mysql_db.py
git commit -m "feat(model): MessageModel 增加 status 列（complete/interrupted）"
```

## Task 1.2: 数据库 status 列迁移

**Files:**
- Create: `scripts/migrations/2026-08-31-add-message-status.sql`
- Modify: 无（手动执行 DDL，与现有手工建表一致）

**Interfaces:**
- Consumes: Task 1.1 的模型列定义
- Produces: DB `conversation_history.status` 列存在

- [ ] **Step 1: 写迁移脚本**

`scripts/migrations/2026-08-31-add-message-status.sql`：

```sql
-- 为 conversation_history 增加 status 列（complete/interrupted）
-- 执行：docker exec -i corporate-rag-mysql mysql -uroot -pfinancial_qa_pass financial_qa < 本文件
ALTER TABLE conversation_history
  ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'complete'
  COMMENT 'complete/interrupted';
```

- [ ] **Step 2: 对线上库执行并验证**

Run: `docker exec corporate-rag-mysql mysql -uroot -pfinancial_qa_pass financial_qa -e "SHOW COLUMNS FROM conversation_history LIKE 'status'"`
Expected: 返回一行 `status varchar(16) ... default 'complete'`

- [ ] **Step 3: 提交**

```bash
git add scripts/migrations/2026-08-31-add-message-status.sql
git commit -m "feat(db): conversation_history 增加 status 列迁移脚本"
```

## Task 1.3: chat_repo.save_message 透传 status

**Files:**
- Modify: `src/infra/db/mysql_db/chat_repo.py:97-107`
- Test: `tests/infra/db/test_mysql_db.py`

**Interfaces:**
- Consumes: Task 1.1 `MessageModel.status`
- Produces: `ChatRepo.save_message(msg)` 将 `msg.status` 写入行（默认 complete）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_save_message_passthrough_status():
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.mysql_db import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"sess-st-{uuid.uuid4().hex[:8]}"
    await chat_repo.save_message(
        MessageModel(session_id=session_id, kb_id="", role="assistant",
                     content="partial", status="interrupted")
    )
    msgs = await chat_repo.get_messages(session_id)
    assert msgs[0].status == "interrupted"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/infra/db/test_mysql_db.py::test_save_message_passthrough_status -v`
Expected: FAIL（返回 status 恒为 complete）

- [ ] **Step 3: 实施**——在 `save_message` 的 `MessageModel(...)` 构造中加一行

```python
            m = MessageModel(
                session_id=msg.session_id,
                kb_id=getattr(msg, "kb_id", ""),
                role=msg.role,
                content=msg.content,
                sources=sources_json,
                status=getattr(msg, "status", "complete"),
                prompt_tokens=getattr(msg, "prompt_tokens", 0),
                completion_tokens=getattr(msg, "completion_tokens", 0),
                total_tokens=getattr(msg, "total_tokens", 0),
                model_name=getattr(msg, "model_name", ""),
            )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/infra/db/test_mysql_db.py::test_save_message_passthrough_status -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/infra/db/mysql_db/chat_repo.py tests/infra/db/test_mysql_db.py
git commit -m "feat(repo): save_message 透传 status 字段"
```

## Task 1.4: PersistenceService 拆分 user/assistant 写入

**Files:**
- Modify: `src/chat/persistence.py`
- Modify: `src/chat/manager.py`（委托方法）
- Test: `tests/chat/test_chat_manager.py`

**Interfaces:**
- Consumes: Task 1.3 `save_message` 透传 status
- Produces:
  - `PersistenceService.save_user_message(session_id, kb_id, user_msg)`
  - `PersistenceService.save_assistant_message(session_id, kb_id, assistant_msg, sources=None, status="complete")`
  - `ChatManager.save_user_async(session_id, kb_id, user_msg)`、`ChatManager.save_assistant_async(session_id, kb_id, assistant_msg, sources=None, status="complete")`

- [ ] **Step 1: 写失败测试**

`tests/chat/test_chat_manager.py` 追加：

```python
@pytest.mark.asyncio
async def test_save_user_and_assistant_async(monkeypatch):
    from src.chat.manager import ChatManager

    calls = []

    async def fake_save_user(session_id, kb_id, user_msg):
        calls.append(("user", session_id, kb_id, user_msg))

    async def fake_save_assistant(session_id, kb_id, assistant_msg, sources, status):
        calls.append(("assistant", session_id, kb_id, assistant_msg, status))

    cm = ChatManager.__new__(ChatManager)
    cm._persistence = type("P", (), {
        "save_user_message": fake_save_user,
        "save_assistant_message": fake_save_assistant,
    })()
    await cm.save_user_async("s1", "kb1", "q")
    await cm.save_assistant_async("s1", "kb1", "a", None, "interrupted")
    assert calls[0] == ("user", "s1", "kb1", "q")
    assert calls[1] == ("assistant", "s1", "kb1", "a", "interrupted")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/chat/test_chat_manager.py::test_save_user_and_assistant_async -v`
Expected: FAIL（`AttributeError: ChatManager has no attribute 'save_user_async'`）

- [ ] **Step 3: 实施**

`src/chat/persistence.py` 的 `PersistenceService` 加两个方法（保留 `save_messages` 供 M1.6 前过渡）：

```python
    async def save_user_message(
        self, session_id: str, kb_id: str, user_msg: str
    ) -> None:
        """写入一条 user 消息（请求开始时调用）。"""
        try:
            from src.infra.db.models.chat import MessageModel

            await self._chat_repo.save_message(
                MessageModel(session_id=session_id, kb_id=kb_id, role="user", content=user_msg)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save user message async: {}", e)

    async def save_assistant_message(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[str] | None = None,
        status: str = "complete",
    ) -> None:
        """写入一条 assistant 消息（完成/中止时调用，status 区分完整与中断）。"""
        try:
            from src.infra.db.models.chat import MessageModel

            sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id,
                    kb_id=kb_id,
                    role="assistant",
                    content=assistant_msg,
                    sources=sources_json,
                    status=status,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save assistant message async: {}", e)
```

`src/chat/manager.py` 的 `ChatManager` 加两个委托方法（`save_messages_async` 旁）：

```python
    async def save_user_async(
        self, session_id: str, kb_id: str, user_msg: str
    ) -> None:
        """异步写入单条 user 消息到 MySQL（请求开始时调用）。"""
        if self._persistence:
            await self._persistence.save_user_message(session_id, kb_id, user_msg)

    async def save_assistant_async(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[str] | None = None,
        status: str = "complete",
    ) -> None:
        """异步写入单条 assistant 消息到 MySQL（完成/中止时调用）。"""
        if self._persistence:
            await self._persistence.save_assistant_message(
                session_id, kb_id, assistant_msg, sources, status
            )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/chat/test_chat_manager.py -v`
Expected: 全部 PASS（含存量）

- [ ] **Step 5: 提交**

```bash
git add src/chat/persistence.py src/chat/manager.py tests/chat/test_chat_manager.py
git commit -m "feat(chat): PersistenceService 拆分 user/assistant 写入方法"
```

## Task 1.5: /api/chat/stream 流程改造（user 同步落库，保持 GET）

**Files:**
- Modify: `src/api/chat.py`（`chat_stream`、`_stream_rag_response`）
- Test: `tests/api/test_stream_flow.py`

**Interfaces:**
- Consumes: Task 1.4 `ChatManager.save_user_async` / `save_session_async`
- Produces: 流式请求开始即写 user（MySQL 同步 + Redis 在 `agent_service.stream_chat` 内保留）

> 注意：**端点方法保持 GET**（GET→POST 推迟到 Task 3.4 与前端 fetchStream 一同落地，避免 M1 破坏前端 EventSource）。顺序约束：Redis 的 user 写入必须发生在 `agent_service.stream_chat` 的 `get_history_async()` **之后**（避免当前 query 作为历史进 prompt）。因此 M1 只把 MySQL user 写入提前到 API 层，Redis 写入保留在 `stream_chat` 内。

- [ ] **Step 1: 写失败测试**（mock 验证 API 层先写 user 再调 stream）

`tests/api/test_stream_flow.py`：

```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_chat_stream_persists_user_before_stream(auth_client, mock_app_service):
    mock_app_service.set_chat_repo = AsyncMock()
    mock_app_service.agent_service.stream_chat = AsyncMock()
    mock_app_service.agent_service.stream_chat.return_value = iter(())
    mock_app_service.save_user_async = AsyncMock()
    mock_app_service.save_session_async = AsyncMock()

    resp = auth_client.get(
        "/api/chat/stream",
        params={"session_id": "s1", "kb_id": "kb1", "query": "营收多少"},
    )
    assert resp.status_code == 200
    mock_app_service.save_session_async.assert_called_once()
    mock_app_service.save_user_async.assert_called_once_with("s1", "kb1", "营收多少")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_stream_flow.py -v`
Expected: FAIL（无 save_user_async 调用）

- [ ] **Step 3: 实施**——`src/api/chat.py` 的 `chat_stream` 保持 GET，锁获取后插入 user 落库：

```python
    user_id = getattr(request.state, "user_id", "") if request else ""

    # per-session 并发锁：Redis 可用时加锁，冲突直接返回 409
    lock_held = False
    redis = svc.chat_manager._redis
    if redis is not None:
        try:
            lock_held = await _acquire_session_lock(redis, session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Session lock skipped (Redis unavailable): {}", e)
        else:
            if not lock_held:
                raise HTTPException(409, "当前会话正在处理中")

    # M1：请求开始同步落 user（session 创建幂等，写入成功后才启动生成）
    await svc.set_chat_repo()
    await svc.save_session_async(session_id, query[:20], kb_id, user_id)
    await svc.save_user_async(session_id, kb_id, query)
    logger.info("user message persisted at request start: session_id={}", session_id)
```

（原有 `_stream_with_lock` 流程与 `_stream_rag_response` 不变；`_persist_conversation` 的 user/session 写入由 Task 1.6 移除。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/ -v`
Expected: 相关用例 PASS（端点仍为 GET，前端 EventSource 不受影响）

- [ ] **Step 5: 更新契约文档并提交**

`docs/agents/api_contract.md` 追加：`/api/chat/stream` 请求开始同步写 user（MySQL），端点方法保持 GET（POST 迁移在 streaming-decouple M3.4）。

```bash
git add src/api/chat.py tests/api/ docs/agents/api_contract.md
git commit -m "feat(api): /api/chat/stream 请求开始同步落 user（保持 GET）"
```

## Task 1.6: _persist_conversation 瘦身为仅写 assistant

**Files:**
- Modify: `src/api/chat.py`（`_persist_conversation`）

**Interfaces:**
- Consumes: Task 1.4 `ChatManager.save_assistant_async`
- Produces: `_persist_conversation(svc, session_id, kb_id, answer, sources, user_id)`（移除 query）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_persist_conversation_writes_only_assistant(auth_client, mock_app_service):
    from src.api.chat import _persist_conversation

    mock_app_service.set_chat_repo = AsyncMock()
    mock_app_service.save_assistant_async = AsyncMock()
    mock_app_service.save_user_async = AsyncMock()

    await _persist_conversation(
        mock_app_service, "s1", "kb1", "完整回答", [], "u1"
    )
    mock_app_service.save_assistant_async.assert_awaited_once()
    mock_app_service.save_user_async.assert_not_called()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_stream_flow.py::test_persist_conversation_writes_only_assistant -v`
Expected: FAIL（当前签名含 query 且调用 save_messages_async）

- [ ] **Step 3: 实施**——`_persist_conversation` 改为：

```python
async def _persist_conversation(
    svc: AppService,
    session_id: str,
    kb_id: str,
    answer: str,
    sources: list[str],
    user_id: str = "",
) -> None:
    """流结束后异步写 assistant 消息到 MySQL，带重试。"""
    await svc.set_chat_repo()

    async def retry(factory, max_retries=3, initial_interval=0.5, backoff=2.0):
        for i in range(max_retries):
            try:
                await factory()
                return
            except Exception as e:  # noqa: BLE001
                if i < max_retries - 1:
                    await asyncio.sleep(initial_interval * (backoff**i))
                else:
                    logger.warning("Persist failed after {} retries: {}", max_retries, e)

    await retry(
        lambda: svc.save_assistant_async(session_id, kb_id, answer, sources, "complete")
    )
```

`app_service` 增加 `save_assistant_async` 委托（转发到 `chat_manager.save_assistant_async`）。`_stream_rag_response` 末尾的调用改为 `_persist_conversation(svc, session_id, kb_id, full_answer, sources, user_id)`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/ -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/chat.py src/services/app_service.py tests/api/
git commit -m "refactor(api): _persist_conversation 瘦身为仅写 assistant"
```

## Task 1.7: ask_user 超时文案改引导推荐

**Files:**
- Modify: `src/config/const.py`（`SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT`）
- Modify: `tests/services/test_agent_service.py`（若有断言引用旧文案）

**Interfaces:**
- Produces: 超时返回文案 `"（用户因超时未填写内容）请基于已有上下文给出推荐方案。"`

- [ ] **Step 1: 写失败测试**

`tests/agents/test_ask_tools.py` 或对应测试文件：

```python
@pytest.mark.asyncio
async def test_ask_user_timeout_text_is_guidance():
    from src.agents.tools import ask_tools
    from src.config.const import SSEInteractionTexts

    # 直接验证超时文案是引导而非 Error 前缀
    assert not SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT.startswith("Error:")
    assert "推荐方案" in SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/ -v`
Expected: FAIL（当前文案以 `Error:` 开头）

- [ ] **Step 3: 实施**——`src/config/const.py` 中：

```python
    # ask_user 等待超时文案：超过 ASK_USER_TIMEOUT 未获答案时作为工具结果
    # 给 LLM，引导其基于已有上下文给出推荐方案（而非报错）
    ASK_USER_TIMEOUT_TEXT: str = "（用户因超时未填写内容）请基于已有上下文给出推荐方案。"
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/ -v`
Expected: 全部 PASS（更新引用旧文案的断言）

- [ ] **Step 5: 提交**

```bash
git add src/config/const.py tests/agents/
git commit -m "feat(config): ask_user 超时文案改为引导 LLM 给推荐方案"
```

## Task 1.8: created_at 顺序回归测试

**Files:**
- Test: `tests/infra/db/test_mysql_db.py`

**Interfaces:**
- Verifies: user 请求开始写、assistant 完成写 → created_at 顺序天然正确

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_user_created_at_before_assistant():
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.mysql_db import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"sess-ts-{uuid.uuid4().hex[:8]}"
    # 模拟 M1 时序：user 请求开始写，assistant 延迟（流结束）写
    await chat_repo.save_message(
        MessageModel(session_id=session_id, kb_id="", role="user", content="q")
    )
    await asyncio.sleep(1.1)  # 越过 1 秒，模拟生成耗时
    await chat_repo.save_message(
        MessageModel(session_id=session_id, kb_id="", role="assistant",
                     content="a", status="complete")
    )
    msgs = await chat_repo.get_messages(session_id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].created_at <= msgs[1].created_at
```

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/infra/db/test_mysql_db.py::test_user_created_at_before_assistant -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add tests/infra/db/test_mysql_db.py
git commit -m "test(db): user/assistant created_at 顺序回归"
```

## Task 1.9: MessageItem 增加 status 字段

**Files:**
- Modify: `src/api/model/response.py`（`MessageItem`）
- Modify: `src/services/app_service.py`（`get_messages`）
- Modify: `tests/api/test_sessions.py`、`tests/api/mock_data.py`
- Modify: `docs/agents/api_contract.md`

**Interfaces:**
- Consumes: Task 1.1 `MessageModel.status`
- Produces: `MessageItem.status: str`；`AppService.get_messages(session_id) -> list[dict]` 每项含 `status`

- [ ] **Step 1: 写失败测试**

```python
def test_session_messages_include_status(auth_client, mock_app_service):
    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_app_service.get_messages = AsyncMock(
        return_value=[
            make_message("user", "q"),
            make_message("assistant", "a", status="interrupted"),
        ]
    )
    resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})
    data = resp.json()["data"]
    assert data[1]["status"] == "interrupted"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_sessions.py -v`
Expected: FAIL（响应无 status 字段）

- [ ] **Step 3: 实施**

`src/api/model/response.py` 的 `MessageItem` 加：

```python
    status: str = "complete"  # complete / interrupted — 消息状态
```

`src/services/app_service.py` 的 `get_messages` 返回 dict 加：

```python
                "status": row.get("status", "complete"),
```

`tests/api/mock_data.py` 的 `make_message` 支持 `status` 参数并回填。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/ -v`
Expected: 全部 PASS（同步更新依赖 `MessageItem(...)` 的构造处）

- [ ] **Step 5: 更新契约文档并提交**

`docs/agents/api_contract.md` 记录 `MessageItem.status` 字段。

```bash
git add src/api/model/response.py src/services/app_service.py tests/ docs/agents/api_contract.md
git commit -m "feat(api): MessageItem 增加 status 字段（interrupted 展示）"
```

---

# M2 生成进后台任务 + seq 缓冲 + 断连只停推送

## Task 2.1: 新建 StreamingRunManager（任务注册表）

**Files:**
- Create: `src/chat/streaming.py`
- Test: `tests/chat/test_streaming.py`

**Interfaces:**
- Produces:
  - `class StreamingRunManager`：`register(session_id, task, abort_signal)` / `unregister(session_id)` / `is_running(session_id) -> bool` / `get_abort_signal(session_id) -> asyncio.Event | None` / `set_abort(session_id)` / `get_active_session_ids() -> list[str]`

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import pytest
from src.chat.streaming import StreamingRunManager


@pytest.fixture
def mgr():
    return StreamingRunManager()


@pytest.mark.asyncio
async def test_register_unregister_is_running(mgr):
    async def noop():
        await asyncio.sleep(0.01)

    task = asyncio.create_task(noop())
    signal = asyncio.Event()
    mgr.register("s1", task, signal)
    assert mgr.is_running("s1") is True
    await task
    mgr.unregister("s1")
    assert mgr.is_running("s1") is False
    assert mgr.get_abort_signal("s1") is None


def test_set_abort_sets_event(mgr):
    signal = asyncio.Event()
    mgr._abort_signals["s1"] = signal
    mgr.set_abort("s1")
    assert signal.is_set()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/chat/test_streaming.py -v`
Expected: FAIL（`ModuleNotFoundError: src.chat.streaming`）

- [ ] **Step 3: 实施**

`src/chat/streaming.py`：

```python
"""流式生成运行管理 — 单 worker 进程内任务注册表与 abort 信号映射。

后台生成任务由本管理器持有强引用，防止被 GC；同时维护
session_id → abort_signal 映射，供 POST /api/sessions/cancel 触达任务。
"""

import asyncio
from loguru import logger


class StreamingRunManager:
    """进程内任务注册表 + abort 信号映射（单 worker 部署假设）。"""

    def __init__(self) -> None:
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._abort_signals: dict[str, asyncio.Event] = {}

    def register(self, session_id: str, task: asyncio.Task, abort_signal: asyncio.Event) -> None:
        """登记任务与对应 abort 信号；任务完成时由调用方 unregister。"""
        self._session_tasks[session_id] = task
        self._abort_signals[session_id] = abort_signal
        logger.info("streaming task registered: session_id={}", session_id)

    def unregister(self, session_id: str) -> None:
        """注销任务与 abort 信号（任务 done_callback 调用）。"""
        self._session_tasks.pop(session_id, None)
        self._abort_signals.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        """该 session 是否已有活跃生成任务。"""
        task = self._session_tasks.get(session_id)
        return task is not None and not task.done()

    def get_abort_signal(self, session_id: str) -> asyncio.Event | None:
        """取该 session 的 abort 信号；无活跃任务返回 None。"""
        return self._abort_signals.get(session_id)

    def set_abort(self, session_id: str) -> None:
        """置位该 session 的 abort 信号（cancel 接口调用）。"""
        signal = self._abort_signals.get(session_id)
        if signal is not None:
            signal.set()

    def get_active_session_ids(self) -> list[str]:
        """返回所有活跃任务的 session_id（测试/清理用）。"""
        return [sid for sid, t in self._session_tasks.items() if not t.done()]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/chat/test_streaming.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/chat/streaming.py tests/chat/test_streaming.py
git commit -m "feat(chat): StreamingRunManager 任务注册表与 abort 信号映射"
```

## Task 2.2: 事件缓冲（seq + 生命周期）

**Files:**
- Modify: `src/chat/streaming.py`
- Test: `tests/chat/test_streaming.py`

**Interfaces:**
- Consumes: Task 2.1 `StreamingRunManager`
- Produces:
  - `add_event(session_id, etype, payload) -> int`（返回 seq）
  - `clear_buffer(session_id)`
  - `get_events_since(session_id, after_seq) -> list[tuple[int, str, Any]]`
  - `buffer_exists(session_id) -> bool`
  - `has_terminal(session_id) -> bool`
  - `get_buffer_max_seq(session_id) -> int`
  - `sweep_expired()`
  - 常量 `MAX_ITEMS = 2000`、`TTL_SECONDS = 300`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_buffer_seq_and_lifecycle(mgr):
    mgr.clear_buffer("s1")
    seq1 = mgr.add_event("s1", "status", {"stage": "retrieving"})
    seq2 = mgr.add_event("s1", "token", "你好")
    assert seq1 == 1 and seq2 == 2
    assert mgr.buffer_exists("s1") is True
    events = mgr.get_events_since("s1", 1)
    assert events == [(2, "token", "你好")]
    assert mgr.has_terminal("s1") is False

    mgr.add_event("s1", "done", {"trace_id": "t"})
    assert mgr.has_terminal("s1") is True
    mgr.clear_buffer("s1")
    assert mgr.buffer_exists("s1") is False
    assert mgr.get_buffer_max_seq("s1") == 0


@pytest.mark.asyncio
async def test_buffer_cap_drops_oldest(mgr):
    mgr.clear_buffer("s1")
    for i in range(StreamingRunManager.MAX_ITEMS + 10):
        mgr.add_event("s1", "token", f"t{i}")
    assert len(mgr._stream_buffers["s1"]) <= StreamingRunManager.MAX_ITEMS
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/chat/test_streaming.py -v`
Expected: FAIL（`add_event` 不存在）

- [ ] **Step 3: 实施**——`StreamingRunManager` 增加缓冲字段与方法：

```python
    MAX_ITEMS = 2000
    TTL_SECONDS = 300
    _TERMINAL_TYPES = ("done", "error")

    # __init__ 中追加：
    # self._stream_buffers: dict[str, list[tuple[int, str, Any]]] = {}
    # self._buffer_done_at: dict[str, float] = {}
    # self._seq_counters: dict[str, int] = {}

    def clear_buffer(self, session_id: str) -> None:
        """清空该 session 缓冲并重置 seq（新一轮 POST 时调用）。"""
        self._stream_buffers.pop(session_id, None)
        self._buffer_done_at.pop(session_id, None)
        self._seq_counters.pop(session_id, None)

    def add_event(self, session_id: str, etype: str, payload: Any) -> int:
        """追加一条事件，返回其 seq。终态事件登记完成时间供 TTL 清理。"""
        self.sweep_expired()
        seq = self._seq_counters.get(session_id, 0) + 1
        self._seq_counters[session_id] = seq
        buf = self._stream_buffers.setdefault(session_id, [])
        buf.append((seq, etype, payload))
        if len(buf) > self.MAX_ITEMS:
            del buf[:50]
        if etype in self._TERMINAL_TYPES:
            import time as _t
            self._buffer_done_at[session_id] = _t.time()
        return seq

    def get_events_since(self, session_id: str, after_seq: int) -> list[tuple[int, str, Any]]:
        """返回缓冲中 seq 大于 after_seq 的事件（回放用）。"""
        return [it for it in self._stream_buffers.get(session_id, []) if it[0] > after_seq]

    def buffer_exists(self, session_id: str) -> bool:
        """该 session 是否有缓冲。"""
        return session_id in self._stream_buffers

    def has_terminal(self, session_id: str) -> bool:
        """缓冲是否已含终态事件（done/error）。"""
        return any(et in self._TERMINAL_TYPES for _, et, _ in self._stream_buffers.get(session_id, []))

    def get_buffer_max_seq(self, session_id: str) -> int:
        """当前缓冲最大 seq（status 接口返回）。"""
        buf = self._stream_buffers.get(session_id, [])
        return buf[-1][0] if buf else 0

    def sweep_expired(self) -> None:
        """惰性清理：已完成且超过 TTL 的会话缓冲。"""
        if not self._buffer_done_at:
            return
        import time as _t
        now = _t.time()
        expired = [sid for sid, ts in self._buffer_done_at.items() if now - ts > self.TTL_SECONDS]
        for sid in expired:
            self.clear_buffer(sid)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/chat/test_streaming.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/chat/streaming.py tests/chat/test_streaming.py
git commit -m "feat(chat): seq 事件缓冲与当前轮生命周期管理"
```

## Task 2.3: SSE 事件序列化契约（type / payload_for_buffer / from_payload）

**Files:**
- Modify: `src/utils/sse.py`（事件类补成员 + `from_payload`）
- Test: `tests/utils/test_sse_roundtrip.py`

**Interfaces:**
- Produces:
  - 每个 SSE 事件类新增 `type` 属性（与 `to_sse` 的 `event:` 名一致）与 `payload_for_buffer() -> dict`（返回 `to_sse` 序列化的 `data:` 同构 dict）
  - `sse.from_payload(etype: str, payload: dict) -> SSEEvent`（还原事件，供 resume 回放）

> 依赖 Task 2.2 缓冲事件 `(seq, type, payload)` 的 payload 语义：本任务确立"缓冲 payload == 实时 `data:` 内容"，是 resume 与实时渲染同路径的地基。

- [ ] **Step 1: 写失败测试**——9 种事件 round-trip：`to_sse(from_payload(e.type, e.payload_for_buffer())) == to_sse(e)`

```python
import pytest
from src.utils.sse import (
    from_payload, to_sse,
    SSETokenEvent, SSEStatusEvent, SSECitationEvent, SSEDoneEvent,
    SSEErrorEvent, SSEAskUserEvent, SSEAbstentionEvent,
    SSEReasoningDeltaEvent, SSEModelInfoEvent,
)

CASES = [
    SSETokenEvent(token="你好"),
    SSEStatusEvent(stage="retrieving", message="检索中"),
    SSEStatusEvent(stage="retrieving", message="检索中", detail="详情"),
    SSECitationEvent(source="财报.pdf", page=3, snippet="摘要", score=0.9,
                     highlighted_snippet="<mark>摘要</mark>", index=1, kind="kb"),
    SSEDoneEvent(trace_id="trace_x"),
    SSEErrorEvent(error="boom"),
    SSEAskUserEvent(type="clarify", questions=[{"id": "q1", "question": "哪个公司？", "options": ["A", "B"]}]),
    SSEAbstentionEvent(type="abstention", message="未在文档中找到相关数据"),
    SSEReasoningDeltaEvent(reasoning_delta="思考中..."),
    SSEModelInfoEvent(model="qwen-max", is_fallback=False),
]


@pytest.mark.parametrize("ev", CASES, ids=lambda e: type(e).__name__)
def test_sse_roundtrip(ev):
    rebuilt = from_payload(ev.type, ev.payload_for_buffer())
    assert to_sse(rebuilt) == to_sse(ev)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_sse_roundtrip.py -v`
Expected: FAIL（`AttributeError: SSETokenEvent has no attribute 'type'`）

- [ ] **Step 3: 实施**——`src/utils/sse.py`

给 9 个事件类各补 `type` 属性和 `payload_for_buffer()`（以 `SSETokenEvent` 为例，其余同构）：

```python
@dataclass
class SSETokenEvent:
    token: str
    type: str = "token"

    def payload_for_buffer(self) -> dict:
        return {"token": self.token}
```

各类 payload 映射（与对应 `sse_*` 的 `data:` 同构）：

| 事件 | type | payload_for_buffer |
|---|---|---|
| SSETokenEvent | `token` | `{"token": token}` |
| SSEStatusEvent | `status` | `{"stage", "message", 可选 "detail"}` |
| SSECitationEvent | `citation` | `{"source","page","snippet","score","highlighted_snippet","index","kind"}` |
| SSEDoneEvent | `done` | `{"trace_id"}` |
| SSEErrorEvent | `error` | `{"error"}` |
| SSEAskUserEvent | `ask_user` | `{"type", "questions"}` |
| SSEAbstentionEvent | `abstention` | `{"type", "message"}` |
| SSEReasoningDeltaEvent | `reasoning` | `{"delta"}` |
| SSEModelInfoEvent | `model_info` | `{"model", "is_fallback"}` |

模块级还原函数：

```python
def from_payload(etype: str, payload: dict) -> "SSEEvent":
    """由缓冲 payload 还原 SSE 事件（resume 回放用）。"""
    if etype == "token":
        return SSETokenEvent(token=payload["token"])
    if etype == "status":
        return SSEStatusEvent(stage=payload["stage"], message=payload["message"],
                              detail=payload.get("detail"))
    if etype == "citation":
        return SSECitationEvent(source=payload["source"], page=payload["page"],
                                snippet=payload["snippet"], score=payload["score"],
                                highlighted_snippet=payload["highlighted_snippet"],
                                index=payload["index"], kind=payload["kind"])
    if etype == "done":
        return SSEDoneEvent(trace_id=payload.get("trace_id", ""))
    if etype == "error":
        return SSEErrorEvent(error=payload["error"])
    if etype == "ask_user":
        return SSEAskUserEvent(type=payload["type"], questions=payload["questions"])
    if etype == "abstention":
        return SSEAbstentionEvent(type=payload["type"], message=payload["message"])
    if etype == "reasoning":
        return SSEReasoningDeltaEvent(reasoning_delta=payload["delta"])
    if etype == "model_info":
        return SSEModelInfoEvent(model=payload["model"], is_fallback=payload["is_fallback"])
    raise ValueError(f"unknown sse event type: {etype}")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/utils/test_sse_roundtrip.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/sse.py tests/utils/test_sse_roundtrip.py
git commit -m "feat(sse): 事件序列化契约 type/payload_for_buffer/from_payload"
```

## Task 2.4: 生产者拆分（后台任务内图事件→缓冲）

**Files:**
- Modify: `src/services/agent_service.py`
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 2.2 `StreamingRunManager.add_event`
- Produces:
  - `async def _run_generation(session_id, kb_id, query, history, deep_thinking, ctx, manager) -> str`（生产者 coroutine，返回完整回答；内部迭代 `graph.astream_events` + `clarify_channel`，调用 `manager.add_event` 写缓冲）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_run_generation_writes_events_to_buffer(monkeypatch):
    from src.chat.streaming import StreamingRunManager
    from src.services.agent_service import _run_generation
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _convert_event

    mgr = StreamingRunManager()

    async def fake_astream(state):
        yield {"event": "on_chat_model_stream", "data": {"chunk": "你"}}
        yield {"event": "on_chat_model_stream", "data": {"chunk": "好"}}

    fake_graph = type("G", (), {"astream_events": fake_astream})()
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    answer = await _run_generation(
        "s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph
    )
    assert answer == "你好"
    events = mgr.get_events_since("s1", 0)
    assert any(et == "token" for _, et, _ in events)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py::test_run_generation_writes_events_to_buffer -v`
Expected: FAIL（`_run_generation` 不存在）

- [ ] **Step 3: 实施**——`agent_service.py` 新增生产者 coroutine（复用现有 `_convert_event` 的事件映射）：

```python
async def _run_generation(
    session_id: str,
    kb_id: str,
    query: str,
    history: list,
    deep_thinking: bool,
    ctx: RequestContext,
    manager: StreamingRunManager,
    graph=None,
    partial_holder: dict | None = None,
) -> str:
    """后台生成任务：迭代图事件转换为带 seq 事件写入缓冲，返回完整回答。

    Args:
        session_id: 会话 ID
        kb_id: 知识库 ID
        query: 用户查询
        history: 对话历史（不含当前 query）
        deep_thinking: 深度思考开关
        ctx: 请求上下文（含 clarify_channel / abort_signal）
        manager: StreamingRunManager（事件缓冲写入）
        graph: 图实例（测试注入用；默认用 self._graph）
        partial_holder: 可选的 {"text": str} 共享 dict，随 token 产出更新，
            供取消/出错时写 interrupted 部分回答
    """
    initial_state = AgentState.make_initial_state(session_id, kb_id, query, history, deep_thinking)
    event_source = (graph or _current_agent_service()._graph).astream_events(initial_state, version="v2")

    full_answer = ""
    async for item in event_source:
        for event in _convert_event(item, None):
            manager.add_event(session_id, event.type, event.payload_for_buffer())
            if isinstance(event, SSETokenEvent):
                full_answer += event.token
                if partial_holder is not None:
                    partial_holder["text"] = full_answer
    return full_answer
```

> 说明：`_convert_event` 返回的 SSE 事件对象需新增 `type` 属性（事件名）与 `payload_for_buffer()` 方法，使缓冲 payload 与 `to_sse` 输出一致（Task 3.1 保证格式一致性）。这一步先在 SSE 事件类上补充这两个成员并写断言。`_current_agent_service()` 为模块级单例访问器（或改为显式传 graph，避免全局状态）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat(agent): 生产者 coroutine 输出带 seq 事件到缓冲"
```

## Task 2.5: 消费者拆分（SSE 从缓冲读）

**Files:**
- Modify: `src/api/chat.py`
- Test: `tests/services/test_dual_stream.py`

**Interfaces:**
- Consumes: Task 2.4 生产者、Task 2.2 `get_events_since` / `has_terminal`
- Produces: `async def _subscribe_buffer(session_id, manager, after_seq=0)`（SSE 生成器：回放 + tail，遇终态结束）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_subscribe_buffer_replays_then_tails():
    from src.chat.streaming import StreamingRunManager
    from src.api.chat import _subscribe_buffer

    mgr = StreamingRunManager()
    mgr.clear_buffer("s1")
    mgr.add_event("s1", "token", "a")
    mgr.add_event("s1", "token", "b")

    collected = []
    async for event in _subscribe_buffer("s1", mgr, after_seq=0, max_idle=0.2):
        collected.append(event)

    assert len(collected) == 2  # 回放 token a/b（若无新事件则自然结束或超时收尾）
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_dual_stream.py::test_subscribe_buffer_replays_then_tails -v`
Expected: FAIL（`_subscribe_buffer` 不存在）

- [ ] **Step 3: 实施**——`src/api/chat.py`：

```python
async def _subscribe_buffer(
    session_id: str,
    manager: StreamingRunManager,
    after_seq: int = 0,
    max_idle: float = 180.0,
) -> AsyncGenerator[str, None]:
    """SSE 消费者：回放缓冲中 seq>after_seq 的事件并 tail 新事件直到终态。

    Args:
        session_id: 会话 ID
        manager: StreamingRunManager
        after_seq: 起始 seq（刷新后 0，同页重连为 lastSeq）
        max_idle: tail 空闲超时秒数（无新事件超时返回续传超时 error）
    """
    emitted = after_seq
    idle_loops = 0
    while True:
        pending = manager.get_events_since(session_id, emitted)
        if pending:
            idle_loops = 0
            for seq, etype, payload in pending:
                if seq > emitted:
                    emitted = seq
                yield to_sse(SSEEvent.from_payload(etype, payload))
        else:
            if manager.has_terminal(session_id):
                return
            idle_loops += 1
            if idle_loops * 0.3 > max_idle:
                yield to_sse(SSEErrorEvent("续传超时，请刷新页面"))
                return
            await asyncio.sleep(0.3)
```

> 说明：`SSEEvent.from_payload(etype, payload)` 是 Task 2.4 引入的"缓冲 payload → SSE 事件"的还原构造器，保证实时与回放格式一致。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_dual_stream.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/chat.py tests/services/test_dual_stream.py
git commit -m "feat(api): SSE 消费者从缓冲回放+tail"
```

## Task 2.6: abort 触发器反转（仅 cancel）

**Files:**
- Modify: `src/services/agent_service.py`（`_dual_stream` 移除断连置位 abort 逻辑；`stream_chat` 改为启动后台任务）
- Test: `tests/services/test_dual_stream.py`

**Interfaces:**
- Consumes: Task 2.1 `register`/`set_abort`、Task 2.4 `_run_generation`
- Produces: `AgentService.stream_chat` 不再被 SSE 连接生命周期驱动；`_dual_stream` 的 abort 置位移除

- [ ] **Step 1: 写失败测试**——断连（aclose）不置位 abort_signal

```python
@pytest.mark.asyncio
async def test_disconnect_does_not_set_abort():
    from src.services.agent_service import _dual_stream

    abort_signal = asyncio.Event()

    async def fake_events():
        yield {"event": "on_chat_model_stream", "data": {"chunk": "a"}}
        await asyncio.sleep(5)

    gen = _dual_stream(fake_events(), asyncio.Queue(), abort_signal)
    ait = gen.__aiter__()
    await ait.__anext__()
    await gen.aclose()  # 模拟客户端断开
    assert abort_signal.is_set() is False  # 断连不得置位 abort
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_dual_stream.py::test_disconnect_does_not_set_abort -v`
Expected: FAIL（当前 `_dual_stream` finally 置位 abort）

- [ ] **Step 3: 实施**

`_dual_stream` 的 finally 中移除 `abort_signal.set()`，只保留消费侧清理（事件源由生产者任务自行管理）：

```python
    finally:
        # 断连只停止消费，不置位 abort（仅 cancel 置位）
        task_a.cancel()
        await asyncio.gather(task_a, return_exceptions=True)
```

`stream_chat` 改为启动后台任务并返回 SSE 订阅（任务内跑 `_run_generation`，`ctx` 内建并 `register` 到 manager；abort 由 cancel 端点经 manager 置位）。

**顺序约束（P3，prompt 上下文正确性关键）：** `get_history_async()` 必须在 `add_message_async("user")` **之前**，否则当前 query 会作为历史进 prompt。`stream_chat` 启动任务处的固定顺序：

```python
    # 顺序约束：先取历史（不含当前 query），再写 Redis user，再启动任务
    history = await self._chat_manager.get_history_async(session_id) or []
    await self._chat_manager.add_message_async(session_id, "user", query)
    return self._subscribe_stream(...)  # 启动后台任务 + 返回 SSE 订阅
```

配套测试断言"history 不含当前 query"：

```python
@pytest.mark.asyncio
async def test_stream_chat_history_excludes_current_query(monkeypatch):
    from src.services.agent_service import AgentService

    svc = AgentService.__new__(AgentService)
    svc._chat_manager = type("CM", (), {})()
    calls = []

    async def fake_get_history(session_id):
        calls.append(("history", session_id))
        return []

    async def fake_add(session_id, role, content):
        calls.append(("add", role, content))

    svc._chat_manager.get_history_async = fake_get_history
    svc._chat_manager.add_message_async = fake_add

    await svc.stream_chat("kb1", "s1", "营收多少", False)
    # 先 history 后 add
    assert [c[0] for c in calls] == ["history", "add"]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_dual_stream.py tests/services/test_agent_service.py -v`
Expected: 全部 PASS（更新引用"断连即 abort"的旧断言）

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_dual_stream.py tests/services/test_agent_service.py
git commit -m "refactor(agent): abort 触发器反转为仅 cancel"
```

## Task 2.7: 并发防护（注册表 + Redis 锁）

**Files:**
- Modify: `src/api/chat.py`
- Test: `tests/api/test_stream_flow.py`

**Interfaces:**
- Consumes: Task 2.1 `is_running`
- Produces: POST 先查注册表再取 Redis 锁；锁/注册表在任务完成时释放

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_chat_stream_conflict_returns_409(mgr):
    from src.api.chat import chat_stream

    # 已有活跃任务
    async def noop():
        await asyncio.sleep(10)

    task = asyncio.create_task(noop())
    mgr.register("s1", task, asyncio.Event())
    assert mgr.is_running("s1") is True
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_stream_flow.py -v`
Expected: 当前无此行为断言，先确认注册表 `is_running` 通过

- [ ] **Step 3: 实施**——`chat_stream` 中，取 Redis 锁之前：

```python
    if streaming_manager.is_running(session_id):
        raise HTTPException(status_code=409, detail="当前会话正在处理中")

    redis = get_redis_client()
    lock_held = await _acquire_session_lock(redis, session_id)
    if not lock_held:
        raise HTTPException(status_code=409, detail="当前会话正在处理中")
```

`streaming_manager` 为模块级单例（`src/api/chat.py` 顶部 `streaming_manager = StreamingRunManager()`）。锁与注册表的释放统一放在后台任务 done_callback 中（Task 2.8 一并实现）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_stream_flow.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/chat.py tests/api/test_stream_flow.py
git commit -m "feat(api): 并发防护先查进程内注册表再取 Redis 锁"
```

## Task 2.8: 删除 _persist_conversation，收尾并入后台任务

**Files:**
- Modify: `src/api/chat.py`
- Modify: `src/services/agent_service.py`
- Test: `tests/api/test_stream_flow.py`

**Interfaces:**
- Consumes: Task 1.4 `save_assistant_async`、Task 2.4 `_run_generation`
- Produces: assistant 收尾（complete/interrupted/cancelled）在后台任务内完成；`_persist_conversation` 删除

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_background_task_finalizes_assistant_on_cancel(mgr):
    # 后台任务收到 abort 后，已产出 token 落 interrupted
    from src.api.chat import _start_generation

    calls = []
    fake_svc = type("S", (), {
        "save_assistant_async": AsyncMock(side_effect=lambda *a, **k: calls.append(("assistant", k.get("status")))),
    })()
    await _start_generation(fake_svc, "s1", "kb1", "部分回答", [], cancelled=True)
    assert ("assistant", "interrupted") in calls
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_stream_flow.py::test_background_task_finalizes_assistant_on_cancel -v`
Expected: FAIL（`_start_generation` 不存在）

- [ ] **Step 3: 实施**

后台任务 coroutine（`api/chat.py`）包裹生产者并负责收尾。生产者的"已产出 token"通过共享的可变 dict 暴露：

```python
async def _run_with_finalize(
    svc: AppService,
    session_id: str,
    kb_id: str,
    partial_holder: dict,
    answer_builder,
    manager: StreamingRunManager,
    abort_signal: asyncio.Event,
    release_lock,
) -> None:
    """后台任务主体：跑生成，完成后按结果收尾落库，finally 释放锁并注销。

    Args:
        partial_holder: 生产者写入的 {"text": 已产出 token} 共享 dict，
            取消/出错时据此写 interrupted 部分回答
        answer_builder: 可调用对象，执行生成并更新 partial_holder["text"]
    """
    try:
        full_answer = await answer_builder()
    except asyncio.CancelledError:
        partial = partial_holder["text"]
        if partial:
            await svc.save_assistant_async(session_id, kb_id, partial, [], "interrupted")
        manager.add_event(session_id, "done", {"cancelled": True})
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("generation failed: {}", e)
        partial = partial_holder["text"]
        if partial:
            await svc.save_assistant_async(session_id, kb_id, partial, [], "interrupted")
        manager.add_event(session_id, "error", str(e))
    else:
        await svc.save_assistant_async(session_id, kb_id, full_answer, [], "complete")
        manager.add_event(session_id, "done", {"trace_id": current_trace_id.get() or ""})
    finally:
        release_lock()
        manager.unregister(session_id)
```

> 说明：`answer_builder` 是 Task 2.4 `_run_generation` 的闭包包装——签名改为接收 `partial_holder` 并在产出每个 token 时更新 `partial_holder["text"]`；`_run_generation` 返回完整回答。删除 `_persist_conversation`，`chat_stream` 不再 `create_task(_persist_conversation(...))`。启动后台任务处：

```python
    partial_holder: dict = {"text": ""}
    abort_signal = asyncio.Event()

    async def answer_builder():
        return await _run_generation(
            session_id, kb_id, query, history, deep_thinking, ctx,
            streaming_manager, partial_holder=partial_holder,
        )

    task = asyncio.create_task(_run_with_finalize(
        svc, session_id, kb_id, partial_holder, answer_builder,
        streaming_manager, abort_signal, release_lock,
    ))
    streaming_manager.register(session_id, task, abort_signal)
    task.add_done_callback(lambda _t: release_lock())
    task.add_done_callback(lambda _t: streaming_manager.unregister(session_id))
```

（`history` 取自 Task 2.6 `stream_chat` 内 `get_history_async` 的结果；`ctx` 在后台任务内创建并 `current_request_ctx.set`，`clarify_channel` 挂到 ctx。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_stream_flow.py tests/services/test_agent_service.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/chat.py src/services/agent_service.py tests/api/test_stream_flow.py
git commit -m "refactor(api): 后台任务统一收尾 assistant，删除 _persist_conversation"
```

## Task 2.9: 启动时清空 chat_lock

**Files:**
- Modify: `src/main.py`（lifespan）
- Test: 手动验证

**Interfaces:**
- Consumes: `get_redis_client`
- Produces: 应用启动清空 `chat_lock:*`

- [ ] **Step 1: 实施**——`src/main.py` 的 `lifespan` 中：

```python
    logger.info("财务问答 API 正在启动")
    _warmup_chromadb()
    _clear_stale_chat_locks()
    yield
    logger.info("财务问答 API 正在关闭")
```

新增：

```python
def _clear_stale_chat_locks() -> None:
    """启动时清空 chat_lock:* 键：重启后进程内无任何生成任务，残留锁必然过期。"""
    try:
        from src.infra.redis_client import get_redis_client

        redis = get_redis_client()
        keys = redis.keys("chat_lock:*")
        if keys:
            redis.delete(*keys)
            logger.info("cleared {} stale chat locks at startup", len(keys))
    except Exception as e:  # noqa: BLE001
        logger.warning("clear stale chat locks failed: {}", e)
```

- [ ] **Step 2: 手动验证**——设置一个假锁再重启 app，确认被清

```bash
docker exec corporate-rag-redis redis-cli SET chat_lock:test 1 EX 180
docker compose restart app
docker exec corporate-rag-redis redis-cli EXISTS chat_lock:test   # 期望 0
```

- [ ] **Step 3: 提交**

```bash
git add src/main.py
git commit -m "feat(main): 启动时清空残留 chat_lock 键"
```

---

# M3 续接 / 状态 / 取消接口 + 前端

## Task 3.1: GET /api/sessions/events（resume，含空闲超时）

**Files:**
- Modify: `src/api/sessions.py`
- Test: `tests/api/test_streaming_endpoints.py`

**Interfaces:**
- Consumes: Task 2.5 `_subscribe_buffer`、Task 2.2 缓冲方法
- Produces: `GET /api/sessions/events?session_id=&after_seq=`（SSE）

- [ ] **Step 1: 写失败测试**

```python
def test_resume_endpoint_replays(auth_client, mock_app_service, mgr):
    mgr.clear_buffer("s1")
    mgr.add_event("s1", "token", "a")
    mgr.add_event("s1", "done", {"trace_id": "t"})
    resp = auth_client.get("/api/sessions/events", params={"session_id": "s1", "after_seq": 0})
    assert resp.status_code == 200
    body = resp.text
    assert "token" in body and "done" in body
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_streaming_endpoints.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 实施**——`sessions.py`：

```python
@router.get("/sessions/events")
async def resume_session_events(
    request: Request,
    session_id: str = Query(...),
    after_seq: int = Query(0),
):
    """SSE 断点续接：回放缓冲 seq>after_seq 事件并 tail 到终态。"""
    user_id = getattr(request.state, "user_id", "")
    session = await svc.get_session_by_id(session_id)
    if not session or (session.get("user_id") and session["user_id"] != user_id):
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)
    if not streaming_manager.buffer_exists(session_id):
        return StreamingResponse(
            to_sse(SSEDoneEvent(trace_id="")) + "\n",
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _subscribe_buffer(session_id, streaming_manager, after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_streaming_endpoints.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/sessions.py tests/api/test_streaming_endpoints.py docs/agents/api_contract.md
git commit -m "feat(api): GET /api/sessions/events 断点续接"
```
> `docs/agents/api_contract.md` 追加 `GET /api/sessions/events`（参数 session_id / after_seq，SSE 事件格式与实时流一致）。

## Task 3.2: GET /api/sessions/task-status

**Files:**
- Modify: `src/api/sessions.py`
- Test: `tests/api/test_streaming_endpoints.py`

**Interfaces:**
- Consumes: Task 2.2 缓冲方法、`svc.get_messages`
- Produces: `GET /api/sessions/task-status?session_id=` → `{status: generating|completed|idle, buffer_seq?}`

- [ ] **Step 1: 写失败测试**

```python
def test_task_status_three_states(auth_client, mock_app_service, mgr):
    # generating：缓冲存在且无终态
    mgr.clear_buffer("s1")
    mgr.add_event("s1", "token", "a")
    r1 = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
    assert r1.json()["data"]["status"] == "generating"

    # completed：缓冲有终态
    mgr.add_event("s1", "done", {})
    r2 = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
    assert r2.json()["data"]["status"] == "completed"

    # idle：无缓冲且无 assistant
    mgr.clear_buffer("s1")
    mock_app_service.get_messages = AsyncMock(return_value=[make_message("user", "q")])
    r3 = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
    assert r3.json()["data"]["status"] == "idle"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_streaming_endpoints.py::test_task_status_three_states -v`
Expected: FAIL（404）

- [ ] **Step 3: 实施**——`sessions.py`：

```python
@router.get("/sessions/task-status")
async def get_task_status(request: Request, session_id: str = Query(...)):
    """查询会话生成任务状态：generating / completed / idle。"""
    user_id = getattr(request.state, "user_id", "")
    session = await svc.get_session_by_id(session_id)
    if not session or (session.get("user_id") and session["user_id"] != user_id):
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)

    streaming_manager.sweep_expired()
    if streaming_manager.buffer_exists(session_id) and not streaming_manager.has_terminal(session_id):
        return ResponseModel(data={"status": "generating", "buffer_seq": streaming_manager.get_buffer_max_seq(session_id)})
    if streaming_manager.buffer_exists(session_id) and streaming_manager.has_terminal(session_id):
        return ResponseModel(data={"status": "completed", "buffer_seq": streaming_manager.get_buffer_max_seq(session_id)})
    msgs = await svc.get_messages(session_id)
    has_assistant = any(m["role"] == "assistant" for m in msgs)
    return ResponseModel(data={"status": "completed" if has_assistant else "idle"})
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_streaming_endpoints.py::test_task_status_three_states -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/sessions.py tests/api/test_streaming_endpoints.py docs/agents/api_contract.md
git commit -m "feat(api): GET /api/sessions/task-status 三态判定"
```
> `docs/agents/api_contract.md` 追加 `GET /api/sessions/task-status`（返回 status 取值 generating/completed/idle 与 buffer_seq）。

## Task 3.3: POST /api/sessions/cancel

**Files:**
- Modify: `src/api/sessions.py`
- Test: `tests/api/test_streaming_endpoints.py`

**Interfaces:**
- Consumes: Task 2.1 `set_abort` / `get_abort_signal`
- Produces: `POST /api/sessions/cancel` body `{session_id}` → `{cancelled: bool}`

- [ ] **Step 1: 写失败测试**

```python
def test_cancel_sets_abort(auth_client, mock_app_service, mgr):
    signal = asyncio.Event()
    mgr._abort_signals["s1"] = signal
    resp = auth_client.post("/api/sessions/cancel", json={"session_id": "s1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["cancelled"] is True
    assert signal.is_set() is True

    resp2 = auth_client.post("/api/sessions/cancel", json={"session_id": "nope"})
    assert resp2.json()["data"]["cancelled"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_streaming_endpoints.py::test_cancel_sets_abort -v`
Expected: FAIL（404）

- [ ] **Step 3: 实施**——`sessions.py`：

```python
@router.post("/sessions/cancel", response_model=ResponseModel)
async def cancel_session(request: Request, body: SessionCancelRequest):
    """主动停止某会话正在进行的生成：置位 abort_signal。"""
    session_id = body.session_id
    user_id = getattr(request.state, "user_id", "")
    session = await svc.get_session_by_id(session_id)
    if not session or (session.get("user_id") and session["user_id"] != user_id):
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)
    if streaming_manager.get_abort_signal(session_id) is not None:
        streaming_manager.set_abort(session_id)
        return ResponseModel(data={"cancelled": True, "session_id": session_id})
    return ResponseModel(data={"cancelled": False, "session_id": session_id, "reason": "no_active_task"})
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_streaming_endpoints.py::test_cancel_sets_abort -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/sessions.py tests/api/test_streaming_endpoints.py docs/agents/api_contract.md
git commit -m "feat(api): POST /api/sessions/cancel 显式停止生成"
```
> `docs/agents/api_contract.md` 追加 `POST /api/sessions/cancel`（请求体 session_id，返回 cancelled 布尔与 no_active_task）。

## Task 3.4: 传输层 fetchStream + /api/chat/stream 改 POST

**Files:**
- Modify: `src/api/model/request.py`（新增 `ChatStreamRequest`）
- Modify: `src/api/chat.py`（端点 GET → POST）
- Modify: `deploy/nginx/html/chat.html`
- Test: 浏览器手动验证（playwright-cli 可选）+ `tests/api/test_stream_flow.py`

**Interfaces:**
- Consumes: 后端 POST `/api/chat/stream`、GET `/api/sessions/events`
- Produces: 后端 `POST /api/chat/stream`（body：`ChatStreamRequest`）；前端 `fetchStream(url, options, handlers)`、`parseSSE(buffer, onEvent)`、`startStream(query)`

> 此任务同时完成 GET→POST 迁移（P1 决策：推迟到与前端传输层一起落地，避免 M1 破坏 EventSource）。

- [ ] **Step 1: 后端端点改 POST**

`src/api/model/request.py` 追加：

```python
class ChatStreamRequest(BaseModel):
    """流式问答请求体（POST /api/chat/stream）。"""

    session_id: str  # 会话 ID
    kb_id: str  # 知识库 UUID（空串表示跨库搜索）
    query: str  # 用户问题
    deep_thinking: bool = False  # 深度思考开关
```

`src/api/chat.py` 的 `chat_stream` 改为 POST + body（user 落库逻辑从 Task 1.5 保留）：

```python
@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatStreamRequest,
    svc: AppService = Depends(get_app_service),
):
    session_id = body.session_id
    kb_id = body.kb_id
    query = body.query
    user_id = getattr(request.state, "user_id", "")
    # ...（Task 1.5 的锁获取 + save_session + save_user 逻辑不变）...
    return StreamingResponse(
        _stream_with_lock(svc, redis, session_id, lock_held, kb_id, query, user_id, body.deep_thinking),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

更新 `tests/api/test_stream_flow.py` 的请求断言为 `POST` + json；`docs/agents/api_contract.md` 记录端点改 POST。

- [ ] **Step 2: 运行确认后端通过**

Run: `pytest tests/api/test_stream_flow.py -v`
Expected: PASS

- [ ] **Step 3: 前端实施**——替换 `startSSE`（约 chat.html:1462 起），新增：

```javascript
// ── SSE 传输层：fetch + getReader 手动解析（替代原生 EventSource）──
function fetchStream(url, options, handlers) {
  const controller = new AbortController();
  const start = async () => {
    const resp = await fetch(url, { ...options, signal: controller.signal });
    if (!resp.ok || !resp.body) {
      handlers.onError && handlers.onError(new Error('HTTP ' + resp.status));
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        parseSSE(frame, handlers);
      }
    }
    handlers.onClose && handlers.onClose();
  };
  start().catch((e) => handlers.onError && handlers.onError(e));
  return { controller, url };
}

function parseSSE(frame, handlers) {
  const lines = frame.split('\n');
  let eventName = 'message';
  let data = '';
  for (const line of lines) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return;
  const payload = JSON.parse(data);
  const h = handlers[eventName];
  h && h(payload);
}
```

`startStream(query)` 改为 `fetch POST /api/chat/stream` + `fetchStream`，把现有 `source.addEventListener('status'|'token'|...)` 的回调移入 `handlers`。

- [ ] **Step 4: 手动验证**——启动服务，发问，确认流式渲染与现状一致

Run: 打开 `http://localhost/chat.html`，发一条消息，观察 token/status/citation 正常渲染
Expected: 流式行为与改造前一致

- [ ] **Step 5: 提交**

```bash
git add src/api/model/request.py src/api/chat.py deploy/nginx/html/chat.html tests/api/ docs/agents/api_contract.md
git commit -m "feat: /api/chat/stream 改 POST，前端传输层换 fetch+getReader"
```

## Task 3.5: 前端 lastSeq + 断线续接 + 刷新恢复

**Files:**
- Modify: `deploy/nginx/html/chat.html`

**Interfaces:**
- Consumes: Task 3.1 resume 接口、Task 3.2 status 接口
- Produces: `lastSeq` 状态；`onStreamError()` 断线重连走 resume；`initPage()` 刷新后 status 判断

- [ ] **Step 1: 实施**

- `state.lastSeq = 0`，每个事件回调里 `state.lastSeq = payload.seq || state.lastSeq`
- 新增 `resumeStream(sessionId, afterSeq)`：调 `GET /api/sessions/events?session_id=&after_seq=`，复用 `fetchStream` 渲染
- `onStreamError`：指数退避（1s/2s/4s，最多 3 次）调 `resumeStream(sessionId, state.lastSeq)`，超过提示刷新
- `initPage`（现有页面加载逻辑，约 chat.html:1816 附近）：加载历史后调 `GET /api/sessions/task-status?session_id=`，若 `generating` → `resumeStream(sessionId, 0)`

- [ ] **Step 2: 手动验证**

Run: 发问后立即刷新页面，确认"继续生成"（历史里显示问题 + resume 回放答案）
Expected: 刷新后能看到正在生成或已完成答案，无重复渲染

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): 前端 lastSeq 记录、断线续接、刷新后 status 恢复"
```

## Task 3.6: 前端停止按钮 + 生成中禁输入

**Files:**
- Modify: `deploy/nginx/html/chat.html`

**Interfaces:**
- Consumes: Task 3.3 cancel 接口
- Produces: 停止按钮 → `POST /api/sessions/cancel`；生成中禁输入 + 409 处理

- [ ] **Step 1: 实施**

- 生成中在输入区旁显示「停止」按钮；点击 → `fetch('/api/sessions/cancel', {method:'POST', body: JSON.stringify({session_id}), headers:{'Content-Type':'application/json'}})` → 显示"已停止"
- 生成中（`state.current === STATE.STREAMING`）禁输入框；收到 `done` 恢复
- 发消息收到 409 → 提示"当前会话正在处理中"并转为查看状态（可 resume）

- [ ] **Step 2: 手动验证**

Run: 发问后点「停止」，确认流停止、历史出现 interrupted 部分回答
Expected: 停止按钮生效、部分回答标记中断

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): 停止按钮 + 生成中禁输入 + 409 处理"
```

## Task 3.7: 前端 interrupted 展示

**Files:**
- Modify: `deploy/nginx/html/chat.html`

**Interfaces:**
- Consumes: Task 1.9 `MessageItem.status`
- Produces: 历史中 `status=interrupted` 的 assistant 气泡显示"回答被中断"标记

- [ ] **Step 1: 实施**——历史渲染处（`loadSessionMessages` 相关）：

```javascript
  if (m.role === 'assistant' && m.status === 'interrupted') {
    el.querySelector('.msg-text').innerHTML =
      escapeHtml(m.content) + '<div class="interrupted-tag">（回答被中断）</div>';
  }
```

- [ ] **Step 2: 手动验证**

Run: 停止生成后刷新页面，历史里中断回答带"（回答被中断）"标记
Expected: 标记显示正确，完整回答无标记

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): 历史展示 interrupted 标记"
```

## Task 3.8: 前端澄清路径适配

**Files:**
- Modify: `deploy/nginx/html/chat.html`

**Interfaces:**
- Consumes: resume 回放的 ask_user 事件
- Produces: resume 到 ask_user 事件时进入澄清输入态；过期澄清不渲染

- [ ] **Step 1: 实施**

- `fetchStream` 的 `ask_user` 处理器复用现有 `renderComposer(payload.questions)`（现有逻辑，约 chat.html:1510）
- resume 回放遇 `done` 且 payload 含 `cancelled`/超时 → 若当前处于澄清态，退出并提示"澄清已失效，请重新提问"

- [ ] **Step 2: 手动验证**

Run: 触发澄清（缺失维度问题）→ 刷新页面 → resume 看到澄清问题 → 回答后任务续答
Expected: 澄清问题可见可答，回答后续答正常

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): resume 后澄清输入态与过期澄清处理"
```

---

# M4 P2 细节与测试收尾

## Task 4.1: 删除会话清理运行态

**Files:**
- Modify: `src/api/sessions.py`（delete_session）
- Test: `tests/api/test_sessions.py`

- [ ] **Step 1: 写失败测试**

```python
def test_delete_session_cancels_running_task(auth_client, mock_app_service, mgr):
    signal = asyncio.Event()
    mgr._abort_signals["s1"] = signal
    mgr.clear_buffer("s1")
    mgr.add_event("s1", "token", "a")

    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1", user_id="test-user-id"))
    mock_app_service.delete_session_and_messages = AsyncMock(return_value=True)

    resp = auth_client.post("/api/sessions/delete", json={"session_id": "s1"})
    assert resp.status_code == 200
    assert signal.is_set() is True          # 任务被取消
    assert mgr.buffer_exists("s1") is False  # 缓冲被清
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_sessions.py::test_delete_session_cancels_running_task -v`
Expected: FAIL（删除未清理运行态）

- [ ] **Step 3: 实施**——`delete_session` 中，删除前：

```python
    # 清理运行态：取消任务、释放锁、清缓冲
    streaming_manager.set_abort(session_id)
    streaming_manager.unregister(session_id)
    streaming_manager.clear_buffer(session_id)
    try:
        await _release_session_lock(get_redis_client(), session_id)
    except Exception:  # noqa: BLE001
        pass
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_sessions.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/sessions.py tests/api/test_sessions.py
git commit -m "feat(api): 删除会话时取消任务、释放锁、清缓冲"
```

## Task 4.2: abstention / reasoning 事件进缓冲

**Files:**
- Modify: `src/services/agent_service.py`（生产者事件映射）
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 2.4 生产者
- Produces: `abstention`、`reasoning`（deep_thinking）事件同样 `add_event` 入缓冲

- [ ] **Step 1: 写失败测试**——mock `_convert_event` 产出 abstention 事件，断言其入缓冲

```python
@pytest.mark.asyncio
async def test_abstention_event_buffered(monkeypatch):
    from src.chat.streaming import StreamingRunManager
    from src.services import agent_service
    from src.services.agent_service import _run_generation
    from src.utils.sse import SSEAbstentionEvent

    mgr = StreamingRunManager()
    seen = []

    def fake_convert(item, capture):
        seen.append(item)
        return [SSEAbstentionEvent()]

    monkeypatch.setattr(agent_service, "_convert_event", fake_convert)

    async def fake_astream(state):
        yield {"event": "on_chain_end", "data": {}}

    fake_graph = type("G", (), {"astream_events": fake_astream})()
    ctx = type("Ctx", (), {"clarify_channel": asyncio.Queue()})()
    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert any(et == "abstention" for _, et, _ in mgr.get_events_since("s1", 0))
```

> 说明：`SSEAbstentionEvent.type` 由 Task 2.4 在 SSE 事件类上补充的 `type` 属性提供（值为 `"abstention"`），与 `to_sse` 的事件名一致。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py::test_abstention_event_buffered -v`
Expected: FAIL（abstention 事件未入缓冲）

- [ ] **Step 3: 实施**——生产者中对 `SSEAbstentionEvent` / reasoning 事件同样调用 `manager.add_event(session_id, event.type, event.payload_for_buffer())`（Task 2.4 的统一路径，此处补全事件类型覆盖并加断言）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat(agent): abstention/reasoning 事件纳入缓冲"
```

## Task 4.3: trace_id 显式传递

**Files:**
- Modify: `src/api/chat.py`（后台任务启动处）
- Test: `tests/api/test_stream_flow.py`

**Interfaces:**
- Consumes: `current_trace_id`
- Produces: 后台任务内日志可关联 trace_id

- [ ] **Step 1: 实施**——创建后台任务时捕获并传入 trace_id：

```python
    trace_id = current_trace_id.get() or ""
    task = asyncio.create_task(
        _run_with_finalize(
            svc, session_id, kb_id, query, answer_builder,
            streaming_manager, abort_signal, release_lock, trace_id=trace_id,
        )
    )
```

`_run_with_finalize` 内 `current_trace_id.set(trace_id)`（contextvar 显式 set，跨任务不自动传播）。

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/api/test_stream_flow.py -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add src/api/chat.py tests/api/test_stream_flow.py
git commit -m "feat(api): 后台任务显式传递 trace_id"
```

## Task 4.4: 存量测试适配

**Files:**
- Modify: `tests/services/test_dual_stream.py`、`tests/services/test_agent_service.py`、`tests/chat/test_chat_manager.py`、`tests/api/test_sessions.py`、`tests/api/test_clarify.py`

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 存量断言与新契约一致

- [ ] **Step 1: 逐文件核对并更新**——重点：
  - `test_dual_stream.py`：移除"断连置位 abort"断言（Task 2.6 已改）
  - `test_agent_service.py`：`stream_chat` 返回值/事件路径变化
  - `test_chat_manager.py`：`save_messages_async` 若被移除则改 `save_user_async`/`save_assistant_async`
  - `test_sessions.py`：`MessageItem` 构造加 status
  - `test_clarify.py`：澄清链路在后台任务下的路径

- [ ] **Step 2: 运行全量测试**

Run: `pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/
git commit -m "test: 存量测试适配 streaming-decouple 契约"
```

## Task 4.5: streaming-run 测试补全

**Files:**
- Modify: `tests/chat/test_streaming.py`
- Modify: `tests/api/test_streaming_endpoints.py`

**Interfaces:**
- Consumes: Task 2.2 缓冲、Task 3.1-3.3 接口
- Produces: 缓冲回放 / cancel 语义 / TTL 清理 / status 三态 / 越权 404 覆盖

- [ ] **Step 1: 补测试**

```python
@pytest.mark.asyncio
async def test_buffer_ttl_sweep(mgr):
    mgr.clear_buffer("s1")
    mgr.add_event("s1", "done", {})
    mgr._buffer_done_at["s1"] -= StreamingRunManager.TTL_SECONDS + 1
    mgr.sweep_expired()
    assert mgr.buffer_exists("s1") is False


def test_resume_endpoint_unauthorized(auth_client, mock_app_service):
    mock_app_service.get_session_by_id = AsyncMock(return_value=None)
    resp = auth_client.get("/api/sessions/events", params={"session_id": "missing"})
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/chat/test_streaming.py tests/api/test_streaming_endpoints.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/chat/test_streaming.py tests/api/test_streaming_endpoints.py
git commit -m "test: streaming-run 缓冲/TTL/越权覆盖"
```

## Task 4.6: 质量门禁

**Files:**
- Modify: 无

**Interfaces:**
- Consumes: 全部任务

- [ ] **Step 1: 运行质量门禁**

```bash
pytest tests/ -v
ruff check .
pyright src/
```

Expected: 全部通过 / 无新 error（存量第三方误报不新增）

- [ ] **Step 2: 清理调试代码**——确认无遗留 `print()`、TODO、调试断点

- [ ] **Step 3: 提交（如有残余改动）**

```bash
git add -A
git commit -m "chore: streaming-decouple 质量门禁收尾"
```
