## Why

当前代码结构主要问题是文件过大（rag_chain.py 699 行、chat_manager.py 410 行）、目录层级模糊（api/ 混入业务逻辑、26 个测试文件平铺在 tests/ 根目录）、以及死代码残留（old/、api/routes/空目录、document_loader.py 无人引用）。这些问题导致 LLM 修改代码时需要来回翻找、新功能定位困难、测试与源码的对应关系不清晰。需要一次结构性重构，让代码布局直接反映架构分层。

## What Changes

- **src/api/**：SSE 格式化函数独立成 `sse_utils.py`，业务处理逻辑下沉到 services/
- **src/services/**（新增）：AppService 变薄为编排入口，拆分 KBService / DocumentService / ChatService
- **src/rag/**（新增）：rag_chain.py 拆为 chain.py(编排) / retrieval.py(检索) / prompt.py(Prompt构建) / stream.py(流式生成)
- **src/chat/**（新增）：chat_manager.py 拆为 manager.py(Redis会话) / persistence.py(MySQL持久化)
- **src/infra/redis_client.py**（新增）：Redis 客户端共享工厂
- **middleware/auth.py**：解除对 AppService 的依赖，改为直接使用 `infra/redis_client`
- **tests/**：目录结构镜像 src/，平铺的 26 个测试文件移入对应子目录
- 删除 `old/`、`src/api/routes/`（空目录）、`src/document_loader.py`（死代码）
- **CLAUDE.md** 追加代码目录结构、层间调用规则、文件大小红线、LLM 自检清单

**BREAKING**: import 路径变更（`from src.app_service` → `from src.services.app_service`，`from src.rag_chain` → `from src.rag.chain` 等）

## Capabilities

### New Capabilities
- `codebase-structure`: 定义 src/ 三层目录结构（api→services→infra）及 tests/ 镜像组织

### Modified Capabilities
无 — 本次重构不改变任何功能行为

## Impact

- 涉及 ~30 个文件的 import 路径变更
- 涉及 ~20 个测试文件的迁移和路径更新
- CLI 入口（`cli/eval_ragas.py`）需更新 import
- 外部行为完全不变（无 API 变更、无数据库变更、无前端变更）
