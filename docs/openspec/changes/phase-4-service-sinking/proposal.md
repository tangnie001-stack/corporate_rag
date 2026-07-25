## Why

Phase 3 消除了同步 RAG 流水线（Path A）和 `asyncio.new_event_loop()` 反模式，但评估发现 api/ 层仍然"太胖"——业务逻辑未下沉到 service 层，api/ 直调 infra/db/config ，且有死代码残留。这些问题导致测试难以隔离、扩展成本高、层间职责模糊。Phase 4 的目标是完成业务下沉和层间清理。

## What Changes

1. **新建 AuthService** — 将 `api/auth.py` 中的登录/注册/令牌验证业务逻辑下沉到 `services/auth_service.py`
2. **新增密码工具模块** — `utils/auth_crypto.py` 独立处理 bcrypt 哈希和密码校验
3. **DocumentService 加 `store_and_process()`** — 将 upload 中的文件存储、DB 写入、后台处理串联起来，API 层只调一个方法
4. **API 层直调修复** — 7 个 api/ 文件中的 infra/db/config 直调改为通过 AppService 委托
5. **清理死代码** — 删除 `rag/chain.py`（RAGChain 零生产调用方）和 `api/sse_utils.py`（桥接文件）
6. **删除失效测试** — 4 个引用已删除 RAGChain 方法的测试文件
7. **超限文件拆分**（延后）— 5 个超过 400 行的文件拆为包，放最后做以免影响前面改动

## Capabilities

### New Capabilities
- `auth-service`: 用户认证的业务逻辑封装，含注册、登录、令牌验证
- `auth-crypto`: 密码加密与校验工具函数
- `document-upload-consolidation`: 文档上传全流程封装，含文件存储、元信息写入、后台处理

### Modified Capabilities
- （无需修改现有 spec，本次纯内部重构，不涉及 spec 级行为变更）

## Impact

- 新增文件：`src/services/auth_service.py`、`src/utils/auth_crypto.py`
- 修改文件：`src/api/auth.py`、`src/api/documents.py`、`src/api/chat.py`、`src/api/sessions.py`、`src/api/kb_eval.py`、`src/api/ragas_generate.py`、`src/api/health.py`、`src/api/llm_test.py`、`src/services/app_service.py`、`src/services/document_service.py`
- 删除文件：`src/rag/chain.py`、`src/api/sse_utils.py`
- 删除测试：`tests/rag/test_chain.py`、`tests/rag/test_stream.py`、`tests/rag/test_prompt.py`、`tests/rag/test_rag_chain_tracing.py`
- 无外部依赖变更
