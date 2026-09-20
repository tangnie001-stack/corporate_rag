# ADR-0008：删除历史手工 SQL 迁移文件

- **Status**：Accepted
- **Date**：2026-09-21
- **Deciders**：项目负责人 + AI 结对
- **Supersedes**：ADR-0007 的「**历史迁移脚本保留**」一条（仅该条；0007 的其余决策不变，故 0007 的 Status 保持 `Accepted`）

## 背景与问题

ADR-0007 在 MySQL 退役时把 `scripts/migrations/*.sql` 按「历史记录」保留，理由是"其内容是历史事实"。复核后发现该理由在这两个文件上不成立：

- `scripts/migrations/2026-08-31-add-message-status.sql` —— 给 `conversation_history` 加 `status` 列
- `scripts/migrations/2026-09-11-add-session-agent.sql` —— 给 `sessions` 加 `agent` 列

两条理由支持推翻「保留」：

1. **不可执行**。两个文件都是 MySQL 方言（`VARCHAR(...) COMMENT '...'`），注释里的执行方式为 `docker exec -i corporate-rag-mysql mysql -uroot -pfinancial_qa_pass financial_qa < 本文件`。该容器与卷已随 ADR-0007 删除，命令在任何现存环境都无法执行。
2. **零信息量**。其改动已完整存在于 `alembic/versions/0001_pg_baseline.py`（`conversation_history.status` 见 baseline:77，`sessions.agent` 见 baseline:222）。它们不是"待应用的迁移配方"，而是"已并入 baseline 的重复项" —— 保留下来只会让读者误以为存在一条可用的手工迁移路径。

这两点合起来构成删除依据：**「历史事实」的保留理由，只在文件本身承载别处没有的信息时成立**。

约束（不可忽略）：

- `docs/adr/` 按 `docs/adr/README.md` 的约定**只追加不可变**，因此不能改写 0007 的那一行。
- 活跃 change `e2e-playwright-regression` 的 `proposal.md:22` 把「`2026-09-11-add-session-agent.sql` 幂等（列已存在时）」列为验收场景，该 change 尚未归档。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A | 按 0007 原决定不动 | 仓库里留两个在现存环境无法执行、内容已被 baseline 覆盖的 SQL；读者会误以为它们是可用迁移配方 | 零风险 |
| B | 删除，并直接改写 ADR-0007 第 67 行 | 违反「只追加不可变」，历史记录被抹改 | 省一份 ADR |
| C | 删除，并追加本 ADR 记录推翻 | 读者需读两份 ADR 才能还原现状 | 符合只追加约定；历史可追溯 |

## 决策

**选 C。** 删除两个文件，并以追加方式记录对 ADR-0007 那一条的推翻。

## 理由

关键判据是**「这个文件是否承载别处没有的信息」**。两个 SQL 承载的信息（加列的 DDL）已被 `0001_pg_baseline.py` 完整覆盖，其 MySQL 方言外壳则随 MySQL 退役而失效 —— 信息量为零、且会误导，故删除优于保留。同时，推翻必须**追加**而非改写：ADR 目录的价值在于"后人能看见当初为什么这么定"，抹改会让这一步消失。

## 后果

**正面**：

- `scripts/migrations/` 目录清空并移除；仓库中不再有"指向已删 MySQL 容器"的可执行命令示例。
- 需求池 C-02 的「残留」项归零（手工 SQL 与 alembic 并存的局面结束）。

**负面 / 接受的代价**：

- 若要查"当时这两列是怎么上线到旧环境的"，只能从 git 历史（`deaec14` 之前的树）取，`scripts/` 下不再有副本。
- 活跃 change `e2e-playwright-regression` proposal.md:22 的该条验收场景失去对象（SQL 幂等验证）。该 change 尚未归档，其正文的处置属该 change 自身范围。

**不解决的问题**（避免后人误以为本 ADR 管了它）：

- **不处理其它悬空命名卷**：Chroma 的 `corporate_rag_chroma_data` / `corporate_rag_chroma_onnx_cache` 等「已无读取方但未清理」的卷属另案（同 ADR-0007）。
- **不改写 ADR-0007 正文**：只追加本 ADR，0007 的一字不改。

## 复查触发条件

- **若将来出现「按时间顺序回放 DDL」的审计要求**：需重新评估是否改由 alembic revision 序列线性承载，而不是恢复手工 SQL。
- **若 `e2e-playwright-regression` 落地时仍需验证该列幂等**：应以 `0001_pg_baseline.py` 的列定义为对象重写该条，而不是恢复被删文件。
