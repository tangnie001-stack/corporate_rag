# database-orm Specification

## Purpose
TBD - created by archiving change migrate-sqlalchemy-async. Update Purpose after archive.
## Requirements
### Requirement: ORM 模型定义

系统 SHALL 使用 SQLAlchemy 2.0 declarative 基类定义所有关系型表对应的 ORM 模型，位于 `src/infra/db/models/` 目录下。

该目录 SHALL 是 ORM 模型的**唯一来源**：SHALL NOT 存在第二套重复的模型定义，SHALL NOT 存在与 `alembic.ini` 实际指向不一致的第二套迁移目录。迁移脚本的 `target_metadata` SHALL 指向同一套模型。

模型 SHALL **不依赖任何 MySQL 方言类型**，使全部表能在 PostgreSQL 方言下渲染出 DDL（迁移由 autogenerate 从该 metadata 生成，MySQL 专有类型会在渲染期直接失败）。

模型 SHALL 保留既有关系型 schema 中用于**查询路径**的索引，SHALL NOT 只保留唯一约束 —— 迁移由 metadata 生成，未在 metadata 中声明的索引不会被建立，且下次 autogenerate 会把"库里有、metadata 里没有"的索引生成为**删除**。

#### Scenario: 模型继承基类

- **WHEN** 定义一个表模型
- **THEN** 它 SHALL 继承自定义的 `Base`（`DeclarativeBase`），并可使用 `IDMixin`/`TimestampMixin` 快速添加 id/created_at/updated_at 字段

#### Scenario: 模型与迁移同源

- **WHEN** 对比迁移脚本生成的表结构与运行时使用的模型
- **THEN** 两者 SHALL 描述同一套表与列，SHALL NOT 出现某列只存在于其中一方

#### Scenario: 无第二套模型定义

- **WHEN** 在仓库内搜索同一张表的 ORM 类定义
- **THEN** 每个表 SHALL 只有一个定义处

#### Scenario: 全部表可在 PostgreSQL 方言下渲染

- **WHEN** 用 PostgreSQL 方言编译 `Base.metadata` 中每一张表
- **THEN** 全部 SHALL 编译成功，SHALL NOT 出现 `CompileError: can't render element of type …`

#### Scenario: 查询路径索引被保留

- **WHEN** 对比既有关系型 schema 与 ORM metadata 中的索引
- **THEN** 既有 schema 中服务于查询路径的索引（按会话查消息、按用户与更新时间列会话、按知识库关联文档）SHALL 在 metadata 中声明，SHALL NOT 只保留唯一约束

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

非关系型实体的搜索数据类（`ChunkResult`、`ChunkQueryResult`）SHALL 从 `src/infra/db/entities/search.py` 搬迁到 `src/infra/db/vector_store/types.py`。

`ChunkResult` SHALL 携带混合检索两路各自的排名字段，使融合前的来源可辨。

#### Scenario: 搬迁后 import

- **WHEN** 模块需要引用 `ChunkResult`
- **THEN** import 路径 SHALL 为 `from src.infra.db.vector_store.types import ChunkResult`
- **AND** `src/infra/db/entities/search.py` SHALL 被删除

#### Scenario: 所有引用方同步更新

- **WHEN** 完成搬迁
- **THEN** 以下**引用被搬迁类型（`ChunkResult` / `ChunkQueryResult`）、且 import 路径需同步更新**的文件的 import 路径 SHALL 同步更新 —— `rag/retrieval.py`、`infra/db/vector_store/__init__.py`、`infra/db/vector_store/pg_store.py`、`infra/db/vector_store/mapping.py`、`services/app_service.py`
- **AND** 未引用被搬迁类型的模块（如 `agents/graph/state.py`）SHALL NOT 出现在此清单中
- **AND** 该清单只列**仍存在**的引用方；已随本变更删除的模块 SHALL NOT 列入（例如进程内 BM25 索引组件已随词法检索切换到 PostgreSQL 全文检索而删除）

#### Scenario: 分路排名字段可读

- **WHEN** 一次混合检索返回结果
- **THEN** 每条 `ChunkResult` SHALL 可读出其在 dense 路与词法路的排名（未出现在该路时标记为缺席）
