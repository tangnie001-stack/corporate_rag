## Why

当前数据库层使用手写 SQL（`src/config/queries.py`，341 行 SQL 字符串）+ `aiomysql` 连接池 + `dataclass` 实体 + Repository 模式。存在三个问题：

1. **表结构定义散落三处** — `queries.py` 的 CREATE TABLE、`entities/` 的 dataclass 字段、Repo 的 SELECT 列列表各写一遍，改字段必须同步改三处
2. **无迁移管理** — 用 `CREATE TABLE IF NOT EXISTS` 启动时建表，schema 变更是手动 ALTER TABLE，无版本控制、无回滚、不可复现
3. **无类型安全** — SQL 字符串中的字段名无 IDE 补全、无编译期检查，`%s` 参数化依赖人的纪律

引入 SQLAlchemy 2.0 async ORM + Alembic 可一次性解决这三个问题，且与项目已有需求池规划（C-01 ORM、C-02 Alembic，均为 P1）完全对齐。

## What Changes

- **新增** `src/infra/db/engine.py` — `create_async_engine` + `async_sessionmaker`
- **新增** `src/infra/db/models/` — 6 个 SQLAlchemy ORM 模型类（替代 `entities/` + `queries.py` 的 DDL）
- **新增** `alembic.ini` + `alembic/` — 数据库迁移管理
- **重写** `src/infra/db/mysql_db/*_repo.py` — 5 个 Repo 内部从手写 SQL 改为 SQLAlchemy ORM 查询，方法签名不变
- **改动** `src/services/app_service.py` — `__init__` 参数从 `mysql_db` 改为 `session_factory`
- **删除** `src/config/queries.py` — 341 行 SQL 字符串不再需要
- **删除** `src/infra/db/entities/` — MySQL dataclass 实体被 ORM 模型替代（`search.py` 保留，非 MySQL 实体）
- **删除** `src/infra/db/mysql_db/pool.py` — `MySQLDB` 类被 `engine.py` + `session_factory` 替代

## Capabilities

### New Capabilities

- `database-orm`: SQLAlchemy 2.0 async ORM 模型定义、Session 生命周期管理、类型安全的查询构建
- `database-migrations`: Alembic 数据库迁移管理，支持 schema 版本控制和回滚

### Modified Capabilities

<!-- 无已有 spec 需要修改 -->

## Impact

- **架构层**：MySQL 连接管理从 `MySQLDB`（自建 aiomysql 池）变为 SQLAlchemy `async_engine`（内置连接池）
- **代码量**：新增 ~600 行（Model + engine + Alembic 配置），删除 ~400 行（queries.py + entities/ + pool.py）
- **测试**：`tests/infra/db/test_mysql_db.py` 需重写为 session_factory 模式；其他使用 `@patch("MySQLDB")` 的测试去掉该 patch
- **数据库**：需要 DROP DATABASE + 重建（表结构通过最终迁移脚本一次性重建）
- **清理**：MySQL + Redis + MinIO + ChromaDB 全量清除（`scripts/clean_all_data.py`）
