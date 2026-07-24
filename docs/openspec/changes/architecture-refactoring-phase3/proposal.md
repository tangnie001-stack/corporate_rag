## Why

RAG 系统经过两轮架构升级后（Phase 1: LangGraph 引入，Phase 2: 分块策略重构），遗留了大量死代码和层间违规。当前 RAGChain 与 LangGraph 两条问答管道并行维护、API 层混入业务逻辑、infra/ 层充当场杂项工具袋。这些技术债不清理会持续增加维护成本和新功能开发阻力。

## What Changes

- **删除同步 RAG 管道（Path A）**：移除 `RAGChain.chat_with_citations()` 及依赖的 6 个子函数，删除 `ChatService`（生产死代码），CLI eval 改用 `graph.ainvoke()`。**BREAKING**: `RAGChain` 精简为仅保留 eval 接口。
- **RAGChain 上帝对象拆分**：模型生命周期和 property 迁至 AgentService 直接管理，查询分类/改写委托给已有模块。
- **业务逻辑下沉到 Service 层**：`api/documents.py` 的 `_enrich_chunk_pages`、`_merge_tiny_chunks`、`_process_document_task` 迁至 `DocumentService`，API 层回归纯路由职责。
- **ChatManager 同步方法清理**：`agent_service.py` 和 `sessions.py` 的两处调用改为 async，删除所有 sync 方法（约 80 行）。
- **AppService 依赖重组**：`chat_manager` 和 `BM25Index` 由 AppService 直接持有，AgentService 从 `models.py` 直接取模型实例。
- **Graph 层修复**：`retrieve_node` 内 `asyncio.new_event_loop()` 反模式消除，改为 async 节点直接 `await search()`。
- **文件搬迁**：`infra/sse_utils.py`、`infra/errors.py`、`infra/desensitize.py` 迁至新建 `utils/` 目录。删除 `infra/chunking/enhancer.py`（死代码）。

## Capabilities

### New Capabilities

- `architecture-tidy`: 架构清理 — 死代码删除、层间规约对齐、文件目录整理。不引入新功能。

### Modified Capabilities

无。本变更不修改外部行为或接口契约。

## Impact

**Source（15 个文件）**:
| 文件 | 操作 |
|------|------|
| `src/services/chat_service.py` | ❌ 删除（死代码） |
| `src/rag/chain.py` | 🔧 删除 `chat_with_citations()` + 6 子函数 + 5 个 query 委托薄壳 |
| `src/services/app_service.py` | 🔧 移除 RAGChain/ChatService 依赖，chat_manager + bm25 直持 |
| `src/services/agent_service.py` | 🔧 改 async 调用，模型从 models.py 取 |
| `src/agents/graph/nodes.py` | 🔧 retrieve_node 改 async |
| `src/agents/graph/workflow.py` | 🔧 确认 async 兼容 |
| `src/api/documents.py` | 🔧 业务逻辑移至 DocumentService |
| `src/services/document_service.py` | 🔧 新增 async process_document，删 sync 版 |
| `src/api/chat.py` | 🔧 `svc.rag_chain.chat_manager` → `svc.chat_manager` |
| `src/api/sessions.py` | 🔧 `svc.rag_chain.chat_manager` → `svc.chat_manager` |
| `src/chat/manager.py` | 🔧 删 sync 方法 |
| `src/infra/sse_utils.py` | 📦 迁至 `src/utils/sse.py` |
| `src/infra/errors.py` | 📦 迁至 `src/utils/errors.py` |
| `src/infra/desensitize.py` | 📦 迁至 `src/utils/desensitize.py` |
| `src/infra/chunking/enhancer.py` | ❌ 删除（死代码） |
| `src/utils/` | 🆕 新建目录 |

**Tests（N 个文件）**：
- `tests/rag/test_chain.py`：删除 `test_chat_with_citations_*` 用例
- `tests/services/test_chat_service.py`：删除
- `tests/services/test_app_service.py`：删除 chat 相关用例
- `tests/chat/test_chat_manager.py`：删除 sync 方法测试

**外部系统**：无变更
