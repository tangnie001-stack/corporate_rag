# database-orm Specification

## Purpose
TBD - created by archiving change migrate-sqlalchemy-async. Update Purpose after archive.

## Requirements

### Requirement: ORM 模型定义

系统 SHALL 使用 SQLAlchemy 2.0 declarative 基类定义所有 MySQL 表对应的 ORM 模型，位于 `src/infra/db/models/` 目录下。

#### Scenario: 模型继承基类

- **WHEN** 定义一个表模型
- **THEN** 它 SHALL 继承自定义的 `Base`（`DeclarativeBase`），并可使用 `IDMixin`/`TimestampMixin` 快速添加 id/created_at/updated_at 字段

### Requirement: 异步引擎与 Session 工厂

系统 SHALL 在 `src/infra/db/engine.py` 中创建 `create_async_engine` 和 `async_sessionmaker`，设置 `expire_on_commit=False`。

#### Scenario: Session 工厂返回 AsyncSession

- **WHEN** 调用 `session_factory()`
- **THEN** 返回 `AsyncSession` 实例

#### Scenario: 配置连接池

- **WHEN** 创建 engine
- **THEN** 连接池 SHALL 配置 `pool_size=10`, `max_overflow=10`, `pool_recycle=3600`

### Requirement: Repository 使用 Session 工厂

所有 Repository 类 SHALL 接收 `session_factory` 作为构造参数，每个方法内部使用 `async with self._sf() as session:` 管理 session 生命周期。

#### Scenario: Repo 方法使用独立 session

- **WHEN** 调用 Repo 的任意 CRUD 方法
- **THEN** 该方法 SHALL 在内部创建和关闭自己的 session，不依赖调用方传入 session

### Requirement: 类型安全的查询

所有数据库查询 SHALL 使用 SQLAlchemy ORM 的 `select()` 构建器，字段名通过 Model 类属性引用（如 `KbModel.name`），不再使用手写 SQL 字符串。

#### Scenario: 使用 ORM 查询

- **WHEN** 执行 `select(KbModel).where(KbModel.user_id == uid)`
- **THEN** 查询字段名在 IDE 中 SHALL 可补全，参数化自动处理，无 SQL 注入风险

### Requirement: 搜索类型搬迁

非 MySQL 实体的搜索数据类（`ChunkResult`、`ChunkQueryResult`）SHALL 从 `src/infra/db/entities/search.py` 搬迁到 `src/infra/db/vector_store/types.py`。

#### Scenario: 搬迁后 import

- **WHEN** 模块需要引用 `ChunkResult`
- **THEN** import 路径 SHALL 为 `from src.infra.db.vector_store.types import ChunkResult`
- **AND** `src/infra/db/entities/search.py` SHALL 被删除

#### Scenario: 所有引用方同步更新

- **WHEN** 完成搬迁
- **THEN** `rag/retrieval.py`、`infra/search/bm25_index.py`、`infra/db/vector_store/__init__.py`、`infra/db/vector_store/search.py`、`agents/graph/state.py`、`services/app_service.py` 的 import 路径 SHALL 同步更新
