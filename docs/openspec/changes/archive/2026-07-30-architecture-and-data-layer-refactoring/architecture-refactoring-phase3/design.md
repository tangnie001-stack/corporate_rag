## Context

当前系统存在三条技术债主线：

1. **两条问答管道并行维护**：`RAGChain.chat_with_citations()`（同步）和 `AgentService.stream_chat()`（LangGraph 异步）功能重叠，前者已无生产调用者。
2. **层间规约违规**：`api/documents.py` 包含分块富化、合并、全流程编排等业务逻辑，违反"API 纯路由"的架构规约。
3. **依赖关系不清晰**：`AppService` 通过 `RAGChain` 间接获取 `chat_manager`/`BM25Index`；`ChatManager` 同步/异步双套方法各有一半调用者；`infra/` 混入纯工具函数。

## Goals / Non-Goals

**Goals:**
- 消除同步 RAG 管道（Path A），删除死代码
- API 层回归纯路由职责，业务逻辑下沉到 Service 层
- 统一 `ChatManager` 调用方式（全部 async）
- `AppService` 直接持有全局依赖，去除 `RAGChain` 中间层
- 清理 `infra/` 目录，纯工具迁入 `utils/`
- 消除 `asyncio.new_event_loop()` 反模式（Graph retrieve_node）

**Non-Goals:**
- 不引入新功能或外部行为变更
- 不修复已有的 `_persist_conversation()` 未被调用的问题（SSE 端点 MySQL 持久化缺失）
- 不处理低优先级的重试逻辑统一（#9）和 import 风格统一（#10）

## Decisions

### 1. CLI eval 路径：`graph.ainvoke()` 替代 `RAGChain.chat_with_citations()`

**方案**：不新增 `stream_chat_raw()` 方法，eval 直接构造 Graph 并调用 `graph.ainvoke()` 获取最终 state。

```python
final_state = graph.ainvoke({
    "kb_id": kb_id, "session_id": session_id, "query": query,
    "trace_id": trace_id, "_history": history,
    "retrieval_retries": 0, "downgraded": False, "downgrade_reason": "",
})
answer = final_state["answer"]
citations = final_state["citations"]
```

**理由**：Graph 已在 `final_state` 中产出完整 `answer` 和 `citations`，无需额外方法。

### 2. `retrieve_node` 改为 async 节点

**方案**：`make_retrieve_node()` 返回的 `retrieve_node` 改为 `async def`，直接 `await search()`，消除 `asyncio.new_event_loop()`。

**理由**：LangGraph 原生支持 async 节点和 sync/async 混合图。改动小，只影响 `nodes.py` 的 `_search()` 辅助函数和 `retrieve_node`。

### 3. `ChatManager` 清理 sync 方法

**方案**：`agent_service.py:66` 的 `add_message()` 改为 `await add_message_async()`；`sessions.py:124` 的 `asyncio.to_thread(cleanup_session)` 改为 `await clear_history_async()`。删除 `ChatManager` 中所有 sync 方法（约 80 行），包含 `_ensure_redis()`、`_get_sync_redis()`。

**理由**：两处调用都在 async 上下文中，纯替换无行为变化。

### 4. 业务逻辑下沉

**方案**：`api/documents.py` 的 `_process_document_task()` 迁至 `DocumentService.async process_document()`，`_enrich_chunk_pages()` 和 `_merge_tiny_chunks()` 为私有辅助方法。同步版 `DocumentService.upload_and_process()` 删除。

**理由**：同步版无生产调用者（仅测试），async 版是实际生产路径。

### 5. `AppService` 依赖重组

**方案**：`AppService` 直接持有 `chat_manager` 和 `bm25`，不再通过 `RAGChain` 间接获取：

```python
class AppService:
    def __init__(self, mysql_db=None, vector_store=None, router=None,
                 chat_manager=None, agent_service=None):
        self.db = mysql_db or MySQLDB()
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.chat_manager = chat_manager or ChatManager()
        self.bm25 = BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        self.agent_service = agent_service or AgentService(
            vector_store=self.vector_store,
            bm25=self.bm25,
            chat_manager=self.chat_manager,
        )
        self.kb = KBService(self.db)
        self.document = DocumentService(self.db, self.vector_store, self.router)
```

`AgentService` 从 `models.py` 直接获取 `llm`/`reranker`（lazy property），不再依赖外部传入。

### 6. 文件搬迁

**方案**：新建 `src/utils/`，迁入 3 个纯工具文件，删除 1 个死代码文件：

| 源路径 | 目标路径 | 原因 |
|--------|---------|------|
| `src/infra/sse_utils.py` | `src/utils/sse.py` | 纯格式化函数，零项目依赖 |
| `src/infra/errors.py` | `src/utils/errors.py` | 纯异常类层级，零项目依赖 |
| `src/infra/desensitize.py` | `src/utils/desensitize.py` | 纯文本处理函数，零项目依赖 |
| `src/infra/chunking/enhancer.py` | ❌ 删除 | 旧版 `ParentChildChunker`，已被 `strategies/parent_child.py` 替代 |

## Risks / Trade-offs

**[风险] 重构范围大，15 个源文件 + 测试文件需同步修改**
→ **Mitigation**：严格执行验证闭环：`pytest tests/ -v` 全通过 + `ruff check .` 无错误。

**[风险] CLI eval 改用 `graph.ainvoke()` 后可能输出不一致**
→ **Mitigation**：重构后用相同评估数据跑 CLI eval，与 baseline 分数对比。

**[风险] `ChatManager` sync 方法删除后，如有未发现的 sync 调用者会编译报错**
→ **Mitigation**：删除前已全局 grep 确认所有调用者。删除后 `pytest` 全量运行可捕获遗漏。
