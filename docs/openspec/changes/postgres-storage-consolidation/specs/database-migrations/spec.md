## MODIFIED Requirements

### Requirement: 第一版迁移

第一版迁移 SHALL 从空数据库创建所有表结构（含分块表），并在清库后执行；SHALL 同时在迁移内创建所需扩展，使迁移成为**唯一且幂等**的 schema 建立入口。

表集合 SHALL 至少包含：`users`、`knowledge_base`、`document`、`sessions`、`conversation_history`、`eval_report`、`feedback`，以及分块表 `chunks`（含向量列与全文检索生成列）。

扩展创建 SHALL 使用 `CREATE EXTENSION IF NOT EXISTS`，SHALL 放在迁移里而 **SHALL NOT 只放在数据库首次初始化的脚本目录** —— 后者仅在数据目录首次初始化时执行，既有数据卷不会重跑、托管实例上根本不参与。

#### Scenario: 清库后迁移

- **WHEN** 执行 `alembic upgrade head`
- **THEN** 全部表（含 `chunks`）SHALL 被创建

#### Scenario: 扩展随迁移创建

- **WHEN** 在一个尚未启用向量扩展的空数据库上执行迁移
- **THEN** 迁移 SHALL 先创建所需扩展再建含向量列的表，SHALL NOT 因扩展缺失而失败

#### Scenario: 迁移幂等

- **WHEN** 在已执行过迁移的数据库上再次执行
- **THEN** SHALL NOT 报错，且 SHALL NOT 重复创建对象

#### Scenario: 单一迁移来源

- **WHEN** 检查仓库中的迁移目录
- **THEN** SHALL 只有一套被 alembic 配置实际指向，SHALL NOT 存在内容不同的第二套
