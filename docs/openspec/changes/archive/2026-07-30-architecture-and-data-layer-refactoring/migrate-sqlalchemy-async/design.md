## Context

当前项目数据库层架构：

```
src/config/queries.py      → 341 行手写 SQL 字符串（DDL + CRUD）
src/infra/db/entities/      → 6 个 dataclass 实体（MySQL 表映射）
src/infra/db/mysql_db/pool.py → MySQLDB 类（aiomysql 连接池）
src/infra/db/mysql_db/*_repo.py → 5 个 Repository 类（手写 SQL 查询）
src/main.py lifespan        → 启动时执行 CREATE TABLE IF NOT EXISTS
```

无 Alembic、无 Schema 版本管理、无类型安全的字段引用。服务层通过 `AppService` 访问 Repo，API 层通过 `Depends(get_app_service)` 注入。

迁移目标：SQLAlchemy 2.0 async ORM + Alembic，保留 Repository 模式，不改 Repo 方法签名。

## Goals / Non-Goals

**Goals:**
- 所有 MySQL 查询从手写 SQL 字符串迁移到 SQLAlchemy ORM 查询构建器
- 引入 Alembic 管理 Schema 版本，支持自动迁移和回滚
- 保留现有 Repository 模式（方法签名不变，Service 层零感知）
- 全链路保持 async（FastAPI async → async session）
- 同时清理 MySQL、Redis、MinIO、ChromaDB 的全量数据

**Non-Goals:**
- 不改动 VectorStore（ChromaDB）的接口，不引入向量 DB 的 ORM
- 不改动业务逻辑（Service 层和 API 层只改依赖注入方式）
- 不引入跨 Repo 事务（当前无此需求，session 生命周期在 Repo 内部管理）

## Decisions

### 1. 选型：SQLAlchemy 2.0 ORM（async 模式）

| 选项 | 结论 |
|------|------|
| 保持现状（手写 SQL + aiomysql） | ❌ 表结构定义散落三处、无迁移管理、无类型安全 |
| SQLAlchemy 2.0 ORM | ✅ 推荐。Python 生态标准，async 原生，Alembic 成熟 |
| SQLModel | ⚠️ 不推荐。当前已有 Repository 抽象，合并模型定义反而破坏分层 |
| SQLAlchemy Core Only | ⚠️ 作为过渡方案可用，但最终仍需 ORM 的 Identity Map 和自动映射 |

### 2. expire_on_commit = False

`async_sessionmaker(expire_on_commit=False)`。Repo 方法内 `commit()` 后 session 关闭，对象脱管。设为 False 后对象仍可读。默认 True 会导致 `DetachedInstanceError`。

风险：commit 后在 session 内改对象再 commit 会刷脏数据。Repo 编码规范已预防（每个方法只 commit 一次，无 commit 后改属性的逻辑）。

### 3. Session 生命周期在 Repo 内部管理

```
class KbRepo:
    async def get_kb(self, id):
        async with self._sf() as session:  ← Repo 内部开/关
            ...
```

不采用调用方传入 session 的模式。理由：审计了所有 Service 方法，不存在一个方法内需要两个不同 Repo 在同一个事务里写数据的场景。保持 Repo 自治，Service 层零感知。

### 4. 异步驱动：aiomysql

`create_async_engine("mysql+aiomysql://...")`。项目已在使用 aiomysql，纯 Python 无编译依赖。asyncmy 性能更优但需要 C 编译环境，且性能瓶颈不在 DB 驱动层（RAG 管线中 LLM 调用占 90%+ 延迟）。

### 5. 一次性迁移 + 全量数据清理

不渐进式迁移。原因：
- 表结构允许重建（开发/测试环境）
- 5 个 Repo 全部改内部实现，不改方法签名
- 全量清理保证迁移后数据一致性

### 6. svc.db 直接访问改为走 Repo 方法

当前两处 `svc.db.get_kb_by_name()` / `svc.db.get_all_kb()` 绕过 AppService 方法直接访问了 `_pool_getter`。迁移后 AppService 不再有 `.db` 属性。改为调用已有委托方法或直接调 Repo（CLI 脚本场景）。

### 7. AppService 默认 session_factory 懒导入

`AppService.__init__` 接受 `session_factory=None`，内部用运行时导入避免任何模块级循环依赖：

```python
class AppService:
    def __init__(self, session_factory=None, ...):
        if session_factory is None:
            from src.infra.db.engine import session_factory as _sf
            session_factory = _sf
        self._kb_repo = KbRepo(session_factory)
```

`engine.py` 的依赖链为 `config + aiomysql`，与 `AppService` 无交叉，不存在循环。

### 8. entities/search.py 搬迁到 vector_store/types.py

`ChunkResult` 和 `ChunkQueryResult` 不是 MySQL 实体，而是 VectorStore 的数据契约。它们被非 Repo 模块广泛引用（retrieval、bm25、graph state、vector_store 自身）。搬迁后所有引用方的 import 路径同步更新。

### 9. EvalReportEntity 替换为 ORM 模型

`AppService.insert_eval_report(report: EvalReportEntity)` 的参数类型改为 `EvalReportModel`。

### 10. src/infra/db/__init__.py 改为空导出

删除 `from src.infra.db.mysql_db import MySQLDB`。文件变为空或仅导出 engine 供调试用。

### 11. lifespan 不执行 Alembic，改为手动执行

lifespan handler 删除 `db.init_db()` 调用。Alembic 迁移在容器启动后手动执行：

```bash
docker compose exec app alembic upgrade head
```

仅在首次部署和 schema 变更时需要执行，日常改代码重启容器不需要。

## Risks / Trade-offs

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| Alembic 迁移文件生成不正确 | 中 | 表结构与 Model 不匹配 | 生成后人工 review DDL，对比旧 CREATE TABLE |
| Session 未正确关闭 → 连接池耗尽 | 低 | 生产宕机 | 所有 Repo 方法使用 `async with self._sf()` context manager |
| ORM N+1 查询 | 中 | 性能退化 | 当前 5 个 Repo 无复杂关系查询（无 `relationship`），不触发 N+1 |
| ChromaDB 清理失败残留 | 低 | 测试环境脏数据 | 使用 `delete_collection` API + `rm -rf ./data/chroma_persist/*` 双重保障 |
| Repo 返回类型变化影响 Service | 低 | AttributeError | 迁移后 ORM 对象字段名与旧 dataclass 一致（`id`、`name` 等），Service 层无感 |
| entities/search.py 搬迁后 import 断链 | 中 | 模块导入报错 | 6 处引用 + 3 处测试 import 同步更新；搬迁后 `pytest` 即可发现所有遗漏 |

## Migration Plan

```bash
# 1. 停止服务
docker compose down

# 2. 全量清理（MySQL + Redis + MinIO + ChromaDB）
python scripts/clean_all_data.py
sudo rm -rf ./data/chroma_persist/*

# 3. 重建容器
docker compose up -d --build

# 4. 等 MySQL 健康检查通过后跑迁移（仅首次和 schema 变更时需要）
docker compose exec app alembic upgrade head

# 5. 验证
pytest tests/ -v
ruff check .
```

回滚方案：`git revert` 当前 commit → 重新部署 → 恢复旧数据卷的快照（如果之前有的话）。数据库层全量清理后没有兼容性回滚路径，所以迁移前需要确认可以接受数据丢失（开发/测试环境）。

## Open Questions

- `clean_all_data.py` 是否合入仓库的 `scripts/` 目录？建议合入并加入 `.gitignore` 排除数据目录
