## MODIFIED Requirements

### Requirement: 第一版迁移

第一版迁移 SHALL 从空数据库创建所有表结构（含分块表），并在清库后执行；SHALL 成为**表结构的唯一建立入口**。

表集合 SHALL 至少包含：`users`、`knowledge_base`、`document`、`sessions`、`conversation_history`、`eval_report`、`feedback`，以及分块表 `chunks`（含向量列与全文检索生成列）。

**扩展创建 SHALL NOT 由迁移承担 —— 这是一个经实测确认的权限不对称。**

实测（2026-09-19，`pgvector/pgvector:pg15` / pgvector 0.8.6）：`vector` 的 `vector.control` **没有 `trusted = true`**，因此非超级用户执行 `CREATE EXTENSION vector` 会得到
`ERROR: permission denied to create extension "vector" / HINT: Must be superuser to create this extension.`
而迁移使用的是**应用的运行账号**（业务表属主，非超级用户）。因此：

- 扩展 SHALL 由**具备超级用户权限的账号**在应用库中执行一次 `CREATE EXTENSION IF NOT EXISTS vector`（幂等）。
- 全新数据目录 SHALL 由数据库初始化脚本在超级用户上下文中完成（该脚本仅在数据目录首次初始化时执行）。
- **既有数据目录 / 托管实例** SHALL 由部署文档给出一次性超级用户命令 —— 因为初始化脚本不会重跑。
- 迁移 SHALL 在建含向量列的表**之前断言扩展已存在**，缺失时 SHALL 以**可操作的失败信息**终止（指明需由具备超级用户权限的账号先创建扩展），SHALL NOT 表现为难以归因的建表错误。

#### Scenario: 清库后迁移

- **WHEN** 前置的扩展创建已完成，执行 `alembic upgrade head`
- **THEN** 全部表（含 `chunks`）SHALL 被创建

#### Scenario: 扩展已在超级用户上下文创建

- **WHEN** 在应用库中以具备超级用户权限的账号执行 `CREATE EXTENSION IF NOT EXISTS vector`
- **THEN** 应用的运行账号 SHALL 能够创建并使用含 `vector` 列的表，SHALL NOT 需要超级用户权限

#### Scenario: 扩展缺失时给出可操作的失败

- **WHEN** 以应用的运行账号执行迁移，而应用库中尚未启用向量扩展
- **THEN** 迁移 SHALL 以指明"需由具备超级用户权限的账号先创建扩展"的信息失败，SHALL NOT 输出裸的 `permission denied` 或建表类型错误

#### Scenario: 迁移幂等

- **WHEN** 在已执行过迁移的数据库上再次执行
- **THEN** SHALL NOT 报错，且 SHALL NOT 重复创建对象

#### Scenario: 单一迁移来源

- **WHEN** 检查仓库中的迁移目录
- **THEN** SHALL 只有一套被 alembic 配置实际指向，SHALL NOT 存在第二套未被指向的迁移目录
