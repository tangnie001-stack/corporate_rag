## Context

项目使用 aiomysql + raw SQL、ChromaDB、BM25 进行数据存取。目前所有层间数据传递使用 `list[dict]`，字段访问靠字符串 key。这导致：
- None 能悄无声息地跨越多个函数（上次 `similarity_search` 误删 `return` 后 None 一路传到 `rrf_fusion` 才崩溃）
- 字段重命名/拼写错误无编译器检查
- 没有 IDE 补全

同时 `mysql_db.py`（883行）和 `vector_store.py`（582行）超过项目 400 行红线。

## Goals / Non-Goals

**Goals:**
- 检索链路（ChromaDB → BM25 → RRF → rerank）全面替换为 `list[ChunkResult]`
- MySQL 查询返回类型化的 Entity dataclass（KbEntity, DocEntity, SessionEntity, MessageEntity, UserEntity, EvalReportEntity）
- `mysql_db.py` 按 domain 拆分为 5 个 Repo
- `vector_store.py` 拆为模块包（embedding/client/store/search）
- AgentState 直接存储 dataclass，消除节点间 dict ↔ dataclass 无效转换
- api/documents.py 违规直接调 vector_store 改为走 service

**Non-Goals:**
- 不引入 ORM（保持 aiomysql + raw SQL）
- 不改 ChunkData（parsers/base.py）类型
- 不改 API 层的 Pydantic 模型
- 不改外部行为需求

## Decisions

### 1. 类型体系：@dataclass(slots=True) 用于内部，Pydantic 用于 API 边界

`@dataclass(slots=True)` 用于 infra/service 层内部传递，零运行时开销。API 边界继续保持 Pydantic。

**已确认的决策：**
- ChunkResult 与 ChunkData 保持不同名，不相互影响
- AgentState 中直接存 dataclass（不做序列化适配）
- get_chunks_paginated 返回 ChunkResult（distance/bm25_score 为 None），不新增 ChunkItem

### 2. 转换边界：在 VectorStore 内部转换 dict → ChunkResult

在 `similarity_search()`、`get_chunks_by_doc_id()`、`get_chunks_paginated()` 内部做转换。`similarity_search_all()` 因调 `similarity_search()` 自动受益。`retrieval.py` 不做二次转换。

### 3. 数据实体统一放在 entities/

所有数据实体 —— 不论来自 MySQL、ChromaDB 还是 BM25 —— 统一放在 `src/infra/db/entities/` 下，按 domain 分文件：

```
src/infra/db/entities/
├── __init__.py          # 导出所有实体
├── search.py            # ChunkResult, ChunkQueryResult（检索结果）
├── kb.py                # KbEntity, KbListItem
├── document.py          # DocEntity
├── chat.py              # SessionEntity, SessionListItem, MessageEntity
├── user.py              # UserEntity
└── eval_report.py       # EvalReportEntity
```

### 4. MySQL Repo 包：mysql_db.py 拆为 mysql_db/ 包

```
src/infra/db/mysql_db/
├── __init__.py       # 导出 MySQLDB + 所有 Repo
├── pool.py           # 连接池管理（__init__ / _get_pool / close / init_db）
├── kb_repo.py        # KbRepo → KbListItem / KbEntity
├── document_repo.py  # DocumentRepo → DocEntity
├── chat_repo.py      # ChatRepo → SessionEntity / SessionListItem / MessageEntity
├── user_repo.py      # UserRepo → UserEntity
└── eval_repo.py      # EvalRepo → EvalReportEntity
```

每个 Repo 通过构造时引用 MySQLDB 的连接池方法，不创建新连接：

```python
class KbRepo:
    def __init__(self, mysql_db: MySQLDB):
        self._pool_getter = mysql_db._get_pool  # 引用连接池方法
```

### 5. ChatManager/PersistenceService 改用 ChatRepo

PersistenceService 不再持有 MySQLDB，改为注入 ChatRepo。ChatManager 的 `set_mysql_db` 改为内部构造 Repo。

### 6. api/documents.py 走 service

把 `get_chunks_paginated` 通过 `app_service.py` 暴露，不直接调 `svc.vector_store`。

### 7. 实施顺序：检索链路优先

**Phase 1**: entities/ → vector_store 拆包 → 检索链路类型化 → AgentState 更新（独立可验证）
**Phase 2**: mysql_db 拆分 → 5 个 Repo → Service/CLI 适配（依赖 Phase 1 的 entities/）

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| LangGraph 序列化 dataclass 报错 | AgentState 走 in-memory，当前不走 checkpoint。如遇序列化问题，在 state 进出时加 to_dict() 转换 |
| mysql_db 拆分后 import 链断裂 | 先改调用方再删旧方法，确保每个 commit 后 pytest 通过 |
| Repo 拆分影响 ChatManager 启动流程 | `set_mysql_db` 内部构造 Repo，对外接口不变 |
| CLI 文件漏改 | 清单已列全 3 个 CLI 文件，Phase 1 一并处理 |
