## Why

当前项目所有层间数据传递使用 `list[dict]`，导致：
- None 静默传递（如 `similarity_search` 误删 `return formatted` 后 None 传到 `rrf_fusion` 才报错）
- 字段名拼写错误只能在运行时暴露（`r.get("contnet")` vs `r.content`）
- 没有 IDE 补全和类型推断，开发效率低

同时 `mysql_db.py`（883行）和 `vector_store.py`（582行）超过 400 行红线需要拆分。

## What Changes

1. **新增 `src/infra/db/entities/`** — 按 domain 分文件存放所有数据实体：MySQL 实体（KbEntity、DocEntity、SessionEntity 等）+ 检索实体（ChunkResult、ChunkQueryResult），与 Repo 一一对应，所有字段带注释
2. **检索链路 dict → dataclass** — `similarity_search` / `BM25 search` / `RRF fusion` / `rerank_results` 全面改为返回 `list[ChunkResult]`
3. **`vector_store.py` 拆为模块包** — `vector_store/__init__.py` + `embedding.py` + `client.py` + `store.py` + `search.py`
4. **`mysql_db.py` 拆为 Repo** — mysql_db.py（仅连接池）+ kb_repo.py + document_repo.py + chat_repo.py + user_repo.py + eval_repo.py，每个 Repo 返回对应 Entity dataclass
5. **调用方适配** — 所有 Service、ChatManager/PersistenceService、CLI、api 层改用 Repo 或新类型
6. **修复 api/documents.py 违规** — 直接调 `svc.vector_store` 改为走 service
7. **类型安全落地** — AgentState 直接存 dataclass；节点间不再有 dict ↔ dataclass 无效转换

## Capabilities

### New Capabilities

- `typed-data-layer`: 统一的数据类型定义和跨层传递规范，覆盖检索链路、MySQL 实体、ChromaDB 查询结果

### Modified Capabilities

> 本次是基础设施重构，不修改外部行为需求。无 spec 级别变更。

## Impact

| 影响范围 | 改动量 | 说明 |
|---------|--------|------|
| `src/infra/db/entities/` | 新增 ~200 行（7 个文件） | 所有数据实体（MySQL + 检索） |
| `src/infra/db/vector_store/` | 拆分 582→5 个文件 | 接口不变，返回类型变 |
| `src/infra/db/mysql_db/` | 拆分 883→7 个文件 | mysql_db/ 包，pool + 5 repos + __init__ |
| `5 个新 Repo 文件` | 新增 ~80 行/个 | kb/document/chat/user/eval |
| `src/rag/retrieval.py` | 修改 ~40 行 | 类型注解 + 调用方式 |
| `src/rag/context.py` | 修改 ~5 行 | 加 slots=True |
| `src/infra/search/bm25_index.py` | 修改 ~15 行 | 返回类型 |
| `src/agents/graph/state.py` | 修改 ~5 行 | TypedDict 字段类型 |
| `src/agents/graph/nodes.py` | 修改 ~30 行 | 类型适配 |
| `src/services/` | 修改 4 个文件 | kb/document/auth/app 改用 Repo |
| `src/chat/persistence.py` | 修改 ~15 行 | 改用 ChatRepo |
| `src/chat/manager.py` | 修改 ~5 行 | 注入方式 |
| `src/api/documents.py` | 修改 ~20 行 | 走 service |
| `src/cli/` | 修改 3 个文件 | 类型引用更新 |
| `tests/` | 修改 ~50 行 | mock/fixture 更新 |
