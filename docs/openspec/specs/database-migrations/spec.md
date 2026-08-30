# database-migrations Specification

## Purpose
TBD - created by archiving change migrate-sqlalchemy-async. Update Purpose after archive.

## Requirements

### Requirement: Alembic 初始化

项目 SHALL 配置 Alembic 迁移工具，支持异步引擎的数据库迁移。

#### Scenario: 异步引擎迁移

- **WHEN** 运行 `alembic upgrade head`
- **THEN** 迁移 SHALL 使用 `create_async_engine` 执行 DDL，通过 `connection.run_sync()` 同步执行

### Requirement: 迁移脚本自动生成

Alembic SHALL 配置为自动对比 `Base.metadata` 与当前数据库生成迁移脚本。

#### Scenario: 生成迁移

- **WHEN** 运行 `alembic revision --autogenerate -m "description"`
- **THEN** 生成迁移脚本 SHALL 包含正确的 CREATE TABLE/ALTER TABLE 语句

### Requirement: 第一版迁移

第一版迁移 SHALL 从空数据库创建所有表结构，在 `scripts/clean_all_data.py` 清库后执行。

#### Scenario: 清库后迁移

- **WHEN** 执行 `alembic upgrade head`
- **THEN** 所有 6 张表（`users`, `knowledge_base`, `document`, `sessions`, `conversation_history`, `eval_report`）SHALL 被创建
