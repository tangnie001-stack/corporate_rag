# ADR-0007：退役 MySQL 回滚基座（容器 / 卷 / 镜像全部清理）

- **Status**：Accepted
- **Date**：2026-09-20
- **Deciders**：用户（决策）；Claude（执行与取证）
- **Supersedes**：无 ADR（被逆转的是 P1 计划 `docs/superpowers/plans/2026-09-19-postgres-storage-p1-relational-base.md` 的 T9「保留 MySQL 卷作回滚基座」决定，属计划而非 ADR，故无 ADR 可取代）

## 背景与问题

**P1 当初的刻意保留**：P1 把关系型从 MySQL 迁到 PostgreSQL 时，**没有**顺手删掉容器 `corporate-rag-mysql` 与命名卷 `corporate_rag_mysql_data`，理由是「旧实现连上空的 MySQL 会启动失败 —— 卷在 = 旧代码能起来」，因此该卷被定为**关系型回滚基座**。P1 计划 T9 记录了这条决定，并据此禁止 `docker compose down -v` 与 `docker volume prune`（P1 的 D10 亦以「回滚依据仍完好」作为验收项）。

**前提已经失效**：旧实现是 **MySQL（关系型）+ Chroma（向量）+ BM25（词法）三套**。P4 Task 11（提交 `235fe20`，2026-09-20）已从磁盘删除 `data/chroma_persist` / `data/chroma` / `data/bm25_index` 三个数据目录，其读取路径更早在 P4 Task 6 移除。三套缺二 ⇒ **完整回滚已不可能**，MySQL 卷只剩「能查旧关系型数据」这一残余价值。

**保留的代价持续存在**：

- 容器常驻约 **110 MB** 内存（决策前提），且已是 compose **孤儿** —— `docker-compose.yml` / `docker-compose.prod.yml` 中已无 `mysql` 服务，`src/`、`scripts/`、`deploy/` 亦无任何引用。
- 该容器带 bind mount `deploy/mysql/init → /docker-entrypoint-initdb.d`。Docker 在容器启动时会**重建**缺失的 bind-mount 源目录，因而在 P4 期间反复触发 P1 守卫 `tests/config/test_no_mysql_leftovers.py::test_deploy_mysql_dir_gone` 失败。

**触发事件**：P4 已归档并推送（`origin/dev-wsl == e423d9f`），回滚路径客观上已关闭；用户据此决定**不再保留** MySQL 容器 / 卷 / 镜像。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| ① | 保留容器与卷（维持 P1 状态） | 常驻约 110 MB；孤儿容器无人引用；容器每次启动重建 `deploy/mysql/init`，触发 P1 守卫偶发失败 | 保留「能查旧关系型数据」这一残余能力 |
| ② | 只删容器，保留卷 | 卷不再被任何容器引用，成为 `docker volume prune` 的目标（须继续守住禁令）；仍**无**实际回滚能力 | 省掉常驻内存与孤儿容器 |
| ③ | **全部清理**（容器 + 卷 + 镜像 + 配置 + 注释） | **旧关系型数据永久不可恢复**（不可逆） | 内存归零；孤儿容器与 bind-mount 隐患一并消除；配置与注释回到事实 |

## 决策

**选 ③。** 用户 2026-09-20 决定全部清理：

- `docker rm -f corporate-rag-mysql`
- `docker volume rm corporate_rag_mysql_data`
- `docker rmi mysql:8.0`
- 删除 `.env`（未跟踪）与 `.env.template`（已跟踪）中的 `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` 五个键及其专属注释行
- 删除 bind-mount 空目录 `deploy/mysql/init`（实测执行时该目录已不存在）
- 改写 `docker-compose.yml` / `docker-compose.prod.yml` 顶部「MySQL 卷是回滚依据」的过时注释为「已清理、回滚不再可能」

## 理由

**最关键的一行：MySQL 只是旧实现三套存储中的一套，另两套（Chroma 语料、BM25 索引）已在 P4 物理删除 ——「三缺二」之后，这个卷不再构成任何可用的回滚路径，其残余价值已不足以抵偿常驻内存与守卫隐患。**

- **回滚事实上不可行**：`git revert` 回 pre-P1 后，旧代码需要 MySQL 有 schema、Chroma 有向量、BM25 有索引；后两者已删，卷留着旧实现也起不来。
- **无消费方**：compose 已无 `mysql` 服务，代码与部署目录零引用，容器是孤儿。
- **隐患可被消除**：容器启动重建 `deploy/mysql/init` 是 P1 守卫反复失败的根因；容器不在即不复现。
- **否决 ①**：以固定内存占用与反复的守卫失败，换取一个已无实际用途的「数据副本」，代价持续、收益为零。
- **否决 ②**：只删容器仍把卷置于 `prune` 命中范围，还保留了「有数据可回滚」的假象，不如一次说清。

## 后果

**正面**：

- 释放常驻内存（约 110 MB）并清掉孤儿容器，Docker 资源与真实拓扑一致。
- `deploy/mysql/init` 不再被 Docker 重建，P1 守卫的偶发失败根因消失。
- `.env` / `.env.template` / compose 注释与实际机制一致，不再留下「卷可回滚」的误导。
- 「回滚路径已关闭」的表述在 MySQL 侧也成立（Chroma 侧见 P4 Task 11 与 ADR-0004）。

**负面 / 接受的代价**：

- **关系型旧数据永久不可恢复**：卷删除即不可逆，无备份。
- **回滚到 pre-P1 实现不再有任何依据**：Chroma 侧已先行关闭（P4 Task 11），本 ADR 关闭最后一侧。

**不解决的问题**（避免后人误以为本 ADR 管了它）：

- **RDS 托管化与 prod 安装不受影响**：属遗留项 **F-16**（RDS 托管化切换）与 **F-17**（prod workers 与连接预算），本 ADR 不涉及。
- **历史迁移脚本保留**：`scripts/migrations/*.sql` 是历史迁移配方，按「历史记录」保留；其中的 `docker exec … corporate-rag-mysql` 现已不可执行，但其内容是历史事实，本次不删。
- **不处理其它悬空命名卷**：Chroma 的 `corporate_rag_chroma_data` / `corporate_rag_chroma_onnx_cache` 等「已无读取方但未清理」的卷属另案。

## 复查触发条件

- **若要重新引入关系型回滚能力**（例如 RDS 迁移前想保留对照数据）：必须重新评估，但届时应**以 PG 侧快照为对象**（备份 / 只读副本），而不是恢复 MySQL —— pre-P1 实现已不可启动。
- **若某流程仍假设存在 `corporate-rag-mysql` 容器**：本 ADR 的清理会使其失败，届时改流程，而非恢复容器。
