## MODIFIED Requirements

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

### Requirement: 搜索类型搬迁

非关系型实体的搜索数据类（`ChunkResult`、`ChunkQueryResult`）SHALL 从 `src/infra/db/entities/search.py` 搬迁到 `src/infra/db/vector_store/types.py`。

`ChunkResult` SHALL 携带混合检索两路各自的排名字段，使融合前的来源可辨。

#### Scenario: 搬迁后 import

- **WHEN** 模块需要引用 `ChunkResult`
- **THEN** import 路径 SHALL 为 `from src.infra.db.vector_store.types import ChunkResult`
- **AND** `src/infra/db/entities/search.py` SHALL 被删除

#### Scenario: 所有引用方同步更新

- **WHEN** 完成搬迁
- **THEN** `rag/retrieval.py`、`infra/search/bm25_index.py`、`infra/db/vector_store/__init__.py`、`infra/db/vector_store/pg_store.py`、`agents/graph/state.py`、`services/app_service.py` 的 import 路径 SHALL 同步更新

#### Scenario: 分路排名字段可读

- **WHEN** 一次混合检索返回结果
- **THEN** 每条 `ChunkResult` SHALL 可读出其在 dense 路与词法路的排名（未出现在该路时标记为缺席）
