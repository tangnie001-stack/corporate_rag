## Context

当前项目是金融文档 RAG 问答系统，代码集中在 `src/` 下。主要问题：

- **文件过大**：`rag_chain.py` 699 行、`chat_manager.py` 410 行、`api/documents.py` 499 行，单一文件承担多职责
- **层次模糊**：`api/documents.py` 混入文档处理逻辑（`_process_document_task`），`api/chat.py` 混入 SSE 格式化函数，API 层与业务层边界不清
- **平铺测试**：26 个测试文件平铺在 `tests/` 根目录，与 `src/` 的模块结构不匹配
- **死代码残留**：`old/` 历史快照、`src/api/routes/` 空目录、`src/document_loader.py` 无人引用

## Goals / Non-Goals

**Goals：**
- `src/` 目录结构清晰反映 `api → services → infra` 三层分层
- 大文件按职责拆分为独立模块（rag_chain.py → rag/ 4 文件，chat_manager.py → chat/ 2 文件）
- 测试目录镜像 `src/` 结构，平铺文件归入子目录
- 清理死代码和空目录
- CLAUDE.md 追加架构规约，约束后续 LLM 修改

**Non-Goals：**
- 不改动外部 API 行为和数据库结构
- 不改动前端代码
- 不引入新功能
- 不改变异步/同步模式（保持 ChatManager 双版本共存）

## Decisions

### 1. 三层架构：api → services → infra

src/ 按职责分三层，禁止跨层调用：

```
src/
├── api/             纯路由层：参数校验→调用 service→返回
├── services/        业务编排层：组合子 service + 基础设施
├── rag/             独立领域模块：RAG 流水线
├── chat/            独立领域模块：对话管理
└── infra/           基础设施层：db/llm/search/chunking/auth
```

**层间规则：**
- api/ 不得直接调用 infra/ 或 config/（必须走 services/）
- services/ 可调用 infra/、rag/、chat/
- middleware/auth.py 直接依赖 infra/，不经过 services/

**考虑过的替代方案：** 铺平 api/ 直接调子 service（删除 AppService）。结论：保留 AppService 作为编排入口，因为 `delete_knowledge_base`（三步骤）、`upload_and_process`（六步骤跨基础设施）、`delete_document`（两步）等操作需要编排，不适合放在路由 handler 或单一子 service 中。

### 2. AppService 定位为编排层

AppService 持有 3 个子 service 实例（KBService、DocumentService、ChatService），对外提供 7 个方法。方法分两类：
- **编排型**（保留在 AppService）：`delete_knowledge_base`、`delete_document`、`upload_and_process`
- **转发型**（委托子 service）：`list_knowledge_bases`、`create_knowledge_base`、`get_documents`、`chat`

转发型方法虽然只是委托，但保留在 AppService 上可以确保 api/ 层 7 个文件只需 import 一个构造函数，而不是各自拼装依赖。

### 3. rag/chain.py 的 chat_with_citations() 拆子方法

不整体改为 async（避免波及 LLM stream、CLI 调用、测试），而是在内部拆为 3-4 个私有方法（`_handle_simple_route`、`_handle_short_query`、`_handle_search_error`、`_handle_normal_flow`），每方法控制在 30 行以内。

**考虑过的替代方案：**
- 方案 A（保持现状 103 行）：放弃，单方法 103 行不利于理解和测试
- 方案 C（全量 async）：放弃，波及链太长（rag → app_service → api → cli → tests）

### 4. ChatManager 保持同步/异步双版本

同步版供 RAGChain（运行在线程池中）使用，异步版供 api/chat.py（FastAPI async handler）使用。统一为 async 的收益（消除约 150 行重复代码）与风险（LLM stream 改为 async 的不确定性）不成比例，暂不处理。

### 5. redis_client 独立为基础设施

从 AppService 移除 `redis_client`，新建 `src/infra/redis_client.py` 提供共享 Redis 客户端工厂。
middleware/auth.py 直接从此导入，不再经过 AppService。

### 6. SSE 格式化函数独立

`api/chat.py` 中 6 个 sse_* 格式化函数（仅依赖 json）提取到 `api/sse_utils.py`。
`chat.py` 中的 `get_query_biased_snippet` 和 `_build_highlighted_snippet` 留在原处（仅 chat 路由内部使用）。

### 7. 测试目录镜像 src/ 结构

```
tests/ 根目录 → 只保留 conftest.py、reset_data.py、test_dependencies.py、test_models.py
其余平铺文件移入对应子目录：
  test_rag_chain* → tests/rag/
  test_chat_manager → tests/chat/
  test_app_service → tests/services/
  test_docx_parser / test_pymupdf_parser / test_txt_parser / test_router / test_base → tests/parsers/
  test_mysql_db / test_vector_store / test_file_store → tests/infra/db/
  test_bm25_index / test_query_router → tests/infra/search/
  test_langfuse / test_prompt_manager_fallback → tests/infra/llm/
  test_chunking / test_chunk_enhancer / test_chunk_validator → tests/infra/chunking/
  test_auth → tests/infra/auth/
  test_middleware / test_api_error → tests/middleware/
  test_settings / test_response_codes → tests/config/
  test_eval_ragas + tests/unit/test_chunk_scorer → tests/eval/
```

### 8. 死代码清理

删除 `old/`、`src/api/routes/`、`src/document_loader.py`。这些内容已被当前代码全面超越，git 历史可追溯。

### 9. 新包 __init__.py 导出策略

| 包 | 导出 | 作用 |
|----|------|------|
| src/services/ | AppService | api/ 层 7 文件统一入口 |
| src/rag/ | RAGChain, RAGContext | app_service + cli + 测试 |
| src/chat/ | ChatManager | rag/chain.py 构造参数 |

### 10. CLAUDE.md 架构规约

追加 `## 代码目录结构` 章节，包含目录树、层间调用规则（❌ 禁止/✅ 允许）、文件大小红线（单文件 ≤400 行、单函数 ≤80 行）。更新 `## 验证` 章节，追加 LLM 代码修改自检清单。

## Risks / Trade-offs

- **[Import 断裂]** 批量更新 ~30 个文件 import 路径，可能遗漏 → 完成后 pytest + ruff 全量验证
- **[测试丢失]** 拆分 test_rag_chain.py（28K 行）为多文件时可能遗漏测试 → 逐个方法对照迁移
- **[rag/chain.py 拆分]** chat_with_citations 拆子方法可能引入回归 → 每拆一步立即运行关联测试
- **[兼容性]** CLI（eval_ragas.py）4 处延迟 import AppService → 统一更新路径

## Migration Plan

一次性全量重构，不渐进。顺序：
1. 追加 CLAUDE.md 规约
2. 清理死代码（删除 old/、api/routes/、document_loader.py）
3. 新增基础设施（infra/redis_client.py）
4. 创建新包（services/、rag/、chat/），保持旧文件作为重定向
5. 迁移一路调用方（api/、middleware/、cli/、conftest）到新路径
6. 旧文件删除
7. 测试目录重组
8. pytest + ruff 验证
