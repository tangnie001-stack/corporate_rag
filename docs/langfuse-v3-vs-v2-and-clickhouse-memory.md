# Langfuse v3 vs v2 差异 & ClickHouse 最小内存评估

> **状态：其路线已被 ADR-0011 取代（2026-09-22）。** 本文是决策前的**冻结分析**，数字与结论**不回写**。实际采纳的是「降至 v2 线」，但版本为 `langfuse/langfuse:2.95.11`（`2.95.12` 只有 GitHub release、**无 Docker tag**）；且**未采用**本文建议的「给 ClickHouse 去 Keeper / 设 `CLUSTER_ENABLED=false`」—— 那条建议的前提是仍用 v3，而现行决策已**整体移除 ClickHouse**。现行决策与代价见 `docs/adr/0011-langfuse-v2-downgrade.md`。

> 适用对象：本项目 `corporate_rag` 的 Langfuse 接入（镜像 tag `:3`，全部服务在 `profiles: ["langfuse"]` 下）。
> 参考对象：`../github/WeKnora`（同为 Langfuse v3 自托管）。
> 来源标注：`【官】`= Langfuse / ClickHouse 官方文档、官方仓库、官方 Changelog/博客；`【项】`= 本项目源码/配置；`【二】`= 二手来源；`【推】`= 基于官方事实的推理。
> 找不到一手依据的一律进 §五「未核实项」。

---

## 一、一句话结论

**v3 相对 v2 是一次「单容器 → 六组件微服务化」的架构手术**：v2 只有 `web + Postgres`，v3 变成 `web + worker + Postgres + ClickHouse + Redis + S3/MinIO`，官方理由是 Postgres 扛不住百万级追踪数据的写入与 OLAP 查询【官：v3 stable release / infrastructure-evolution 博客】。代价是自托管最小资源从「一台小机器」抬升到**官方建议 ≥4 核 / 16 GiB**。

**ClickHouse 官方口径：非平凡查询建议 ≥4 GB 内存**，低于此值「服务器能跑但查询会频繁中止」；**不存在官方承认的 256 MB 可用档位**【官：Install / Requirements】。

**对本项目 A（`mem_limit: 256m` + 挂了 Keeper/集群配置 + 未设 `CLICKHOUSE_CLUSTER_ENABLED`）**：当前配置在功能上能自洽（自带的 Keeper + 单节点 cluster 定义让 `ON CLUSTER` 迁移能跑通），但 **256 MB 远低于任何官方下限，属于高风险配置**。**建议：显式设 `CLICKHOUSE_CLUSTER_ENABLED=false`、去掉 Keeper 配置挂载、并把内存上限提到 ≥2 GB（官方推荐 4 GB）。** 官方明确：单节点下该开关「对性能和高可用没有区别」。

---

## 二、Langfuse v3 vs v2 对照

### 2.1 架构与组件

| 维度 | v2 | v3 | 来源 |
|------|----|----|------|
| 应用容器 | **1 个**（`langfuse/langfuse`，全包） | **2 个**：`langfuse`（web，UI/API）+ `langfuse-worker`（异步处理，无对外端口） | 【官】v3 stable release；discussion #1902 |
| 追踪数据存储 | **Postgres**（与事务数据同库） | **ClickHouse（OLAP）**，存 traces/observations/scores | 【官】v3 stable release |
| 事务数据存储 | Postgres | Postgres（不变） | 【官】同上 |
| 队列/缓存 | 无（直接同步写库） | **Redis/Valkey**（队列 + 缓存 API key/prompt） | 【官】同上 |
| 大对象/原始事件存储 | 无 | **S3 / Blob Store（MinIO 等）** | 【官】同上 |
| 自托管必需服务数 | **2**（web + Postgres）+ 可选 LB | **6**（web + worker + Postgres + ClickHouse + Redis + S3/MinIO）+ LB | 【官】v3 stable release 架构图 |
| 表引擎 | 不适用（PG 表） | 追踪表统一 `ReplacingMergeTree` 族（集群模式为复制版，见 §三.4） | 【官】Langfuse handbook · ClickHouse |

### 2.2 功能差异（v3 解锁、v2 没有）

官方 v3 stable release 明确列出三项曾经只在 Cloud 可用、v3 自托管才开放的能力：

| 能力 | v2 | v3 | 来源 |
|------|----|----|------|
| LLM-as-a-Judge 评估器 | ✗ | ✓（依赖 worker 容器 + 队列） | 【官】v3 stable release |
| Prompt Experiments | ✗ | ✓ | 【官】同上 |
| 批量导出（Batch exports，UI 导出 CSV/JSON） | ✗ | ✓ | 【官】同上 |

### 2.3 性能差异

| 项目 | 官方说法 | 来源 |
|------|---------|------|
| 是否有 v2 vs v3 对比基准数字 | **官方未给出对比基准数字**。只说「可靠性/性能显著提升」 | 【官】v3 stable release |
| 量级描述 | v3「能处理**数百 events/秒**（hundreds of events per second）」 | 【官】v3 stable release |
| 延迟目标（非对比数字） | 一周内数据 p99 <1s、>1 周数据可接受 4s；并指出 v2 时代 prompt 检索 p95 峰值曾达 **7s** | 【官】infrastructure-evolution 博客 |
| 规模描述 | 「从实验走向每分钟处理**数万 events**」 | 【官】infrastructure-evolution 博客 |
| v4 对比措辞 | v4「大项目仪表盘快 ≥10×、首屏从秒级降到毫秒级」（v4 vs v3，非 v2） | 【官】v4 changelog |

> **不要编造 v2→v3 的吞吐/延迟倍数**：官方博客给的是「v2 痛点（7s p95）→ v3 目标（p99<1s）」，不是同口径 A/B 基准。§五列为未核实。

### 2.4 生命周期状态

| 版本 | 状态 | 来源 |
|------|------|------|
| v2 | **已停止安全更新**（「received security updates until end of Q1 2025」，即 ~2025-03 末） | 【官】self-hosting/v2 页 |
| v3 | **仍在支持**（v4 changelog：v3「will receive security patches through January 2027」） | 【官】v4 changelog |
| v4 | 2026-08-17 GA；自托管无强制切换日；要求 ClickHouse ≥25.12 | 【官】v4 changelog |

> 对本项目：`:3` 目前**不是 EOL 版本**，但官方已给出 2027-01 的安全补丁截止点；升级到 v4 需先升 ClickHouse 到 ≥25.12（现用 24.12），属后续规划项，不在本次评估范围。

### 2.5 是否有「v3 轻量/单机」模式？

| 问题 | 官方结论 | 来源 |
|------|---------|------|
| 能否把 ClickHouse 换成别的库（如退回 Postgres） | **官方明确否决**：「We explored building a multi-database adapter to support Postgres for smaller self-hosted deployments… we decided against this path」 | 【官】v3 stable release |
| 最短路径是什么 | **官方 docker-compose 单机模式**（web+worker+4 个存储容器），定位是「give it a try」/开发；单容器 ClickHouse 归为 **Community 支持、仅用于开发**，「not recommended for production workloads」 | 【官】docker-compose 指南；ClickHouse(自托管) 页 |
| 官方给小规模的最低整机建议 | **≥4 核 / 16 GiB**（示例 AWS `t3.xlarge`）+ 足够磁盘（如 100 GiB） | 【官】docker-compose 指南 |
| 生产 HA 建议 | K8s + Helm（Chart v2 起由 ClickHouse Operator 渲染 `ClickHouseCluster` + `KeeperCluster`；ClickHouse 建议 3 副本、Keeper 3 副本） | 【官】ClickHouse(自托管) 页 |

---

## 三、ClickHouse 内存要求

### 3.1 官方原文（服务器内存）

| 口径 | 原文含义 | 来源 |
|------|---------|------|
| **最低 RAM（通用）** | 「We recommend using a minimum of **4GB of RAM** to perform non-trivial queries. The ClickHouse server can run with a much smaller amount of RAM, but queries will then frequently abort.」 | 【官】Install / Requirements |
| 生产/低数据量 | 「For low data volumes, a 1:1 memory-to-storage ratio is acceptable but **total memory shouldn't be below 8GB**.」 | 【官】Sizing and hardware recommendations |
| 内存:核比（M 型通用） | **4 GB : 1 core**（R 型 8:1、C 型 2:1） | 【官】Sizing and hardware recommendations |
| Langfuse 整机建议 | v3 docker-compose VM ≥4 核 / 16 GiB | 【官】Langfuse docker-compose 指南 |
| Langfuse Helm 最小 ClickHouse | requests 2 CPU / 8Gi，limits 16Gi | 【官】Langfuse ClickHouse 页 |

**开发 vs 生产的官方区分**：ClickHouse 官方**没有单列「开发环境最低内存」数字**，只说「可以远低于 4 GB，但查询会频繁中止」；「8 GB 下限」是生产/低数据量口径。Langfuse 官方则把「单容器 ClickHouse」直接标注为**开发用途**。

### 3.2 默认内存行为（决定 256 MB 时实际可用多少）

| 参数 | 默认值 | 关键行为 | 来源 |
|------|--------|---------|------|
| `max_server_memory_usage` | `0`（= 不限，交给 ratio） | 绝对值上限，字节 | 【官】max_server_memory_usage_* |
| `max_server_memory_usage_to_ram_ratio` | **`0.9`** | 占总可用内存比例；且**感知 cgroup**：「When running inside a cgroup with a finite memory limit, **the cgroup's available memory is used instead of the host-wide value**」 | 【官】同上 |

**推算【推】**：容器 `mem_limit: 256m` ⇒ cgroup 可用 ≈ 256 MB ⇒ 启动期硬上限 ≈ `0.9 × 256 MB ≈ **230 MB**`；运行时后台线程还会按 `(常驻内存 + 系统可用内存) × 0.9` 动态下压，进一步压低上限。也就是说，**当前配置下 ClickHouse 进程被允许使用的内存约 230 MB 量级，约为官方建议下限 4 GB 的 1/17**。

> 说明：`0.9` 是 ClickHouse 通用默认值；官方文档未声明 Docker 镜像会覆写它。本次未实测该镜像的 `config.xml` 实际值（§五.3）。

### 3.3 256 MB 能否正常启动与工作？

| 问题 | 结论 | 来源 |
|------|------|------|
| 能否启动 | **官方没有给出「启动失败阈值」这一数字**。官方仅说服务端可以在远小于 4 GB 下运行。可启动 ≠ 可用。 | 【官】Install / Requirements |
| 能否稳定工作 | **不能可靠工作**：官方称内存不足时「查询会频繁中止」；且分析类查询（聚合/JOIN/去重）是 Langfuse dashboard 的主路径 | 【官】Install；Langfuse handbook（FINAL/argMax 高内存消耗） |
| 具体 OOM 阈值 | **未核实**（官方无此数字；未做实测）。 | §五.1 |

### 3.4 Keeper 与 `CLICKHOUSE_CLUSTER_ENABLED`（因果链核实）

这是本次任务的核心判断，逐条给一手证据：

| # | 事实 | 来源 |
|---|------|------|
| 1 | `CLICKHOUSE_CLUSTER_ENABLED` 是 **Langfuse 应用侧**环境变量，默认 **`true`**，定义：「Whether to run ClickHouse commands `ON CLUSTER`. **Set to `false` for single-container setups**」 | 【官】Langfuse ClickHouse(自托管) 页；configuration 页 |
| 2 | 官方 `docker-compose.yml` 自己把该值显式设为 **`false`**（`CLICKHOUSE_CLUSTER_ENABLED: ${CLICKHOUSE_CLUSTER_ENABLED:-false}`），且 **clickhouse 服务不挂任何自定义配置、无 Keeper 服务** | 【官】langfuse/langfuse docker-compose.yml |
| 3 | 为什么默认 true 在单机是错的：官方 handbook——「ClickHouse supports a cluster mode and a single instance mode… an `ON CLUSTER` keyword is required to achieve replication in a multi-node setup, but **throws errors on single-instances without a cluster config**」 | 【官】Langfuse handbook · ClickHouse |
| 4 | Langfuse **自带两套迁移 SQL**（`migrations/clustered/*.sql` 与 `migrations/unclustered/*.sql`），执行哪套由 cluster 开关决定；集群模式需要 `ON CLUSTER` 与 `clusterAllReplicas()`，并要求额外授权 `GRANT READ ON REMOTE`、`GRANT CLUSTER` | 【官】handbook；ClickHouse(自托管) 页 |
| 5 | 集群/复制路径需要 ReplicatedMergeTree 参数：官方列出自托管集群必须提供 `macros` 与 `default_replica_path` / `default_replica_name`（这些正是复制表的路径/副本替换参数） | 【官】ClickHouse(自托管) 页 · Bundled ClickHouse 配置 |
| 6 | 因此设 `false` 的效果：迁移走 unclustered 套（不再 `ON CLUSTER`），**单节点无需 Keeper/ZooKeeper**；官方 Azure 排障条目直接写：「We recommend to set `CLICKHOUSE_CLUSTER_ENABLED=false`… **This should not make any difference on performance or high availability**」 | 【官】ClickHouse(自托管) 页 · Troubleshooting |

**因果链结论（证实）**：本项目 A 未设该变量 ⇒ 走应用默认 `true` ⇒ 需要集群定义与复制表 ⇒ 必须自带 Keeper（否则 `ON CLUSTER`/复制表报错），因此**比项目 B 更重**。项目 B 显式 `false` ⇒ 无需 Keeper。**只要显式改 `false` 即可去掉 Keeper 配置**——这条判断成立。

**功能损失**：`false` 仅去掉「多节点 `ON CLUSTER` DDL + 复制」。单节点部署本就没有副本，故无高可用损失；官方亦声明「对性能和高可用没有区别」。生产多节点 HA 场景官方才建议集群 + Keeper（Helm 建议 ClickHouse 3 副本 / Keeper 3 副本）。

**Keeper 额外内存开销**：官方**未给出「集成 Keeper 相对纯 server 多占多少 MB」这一数字**。可得的一手事实是 ClickHouse 官方（ClickHouse Private 参考）称：「**Keeper holds its entire dataset in memory**, and resident usage grows with the logical metadata size」，并在其企业部署里给 Keeper pod 建议 **8 Gi（moderate）/ 16 Gi（high）**。集成 Keeper 与 server 在同一进程内竞争同一 cgroup 内存，因此本项目 256 MB 下 Keeper 必然进一步挤压可用内存（§五.2）。

### 3.5 项目 A / B ClickHouse 配置对照

| 项 | 项目 A `corporate_rag` | 项目 B `WeKnora` |
|----|------------------------|------------------|
| 镜像 | `clickhouse/clickhouse-server:24.12-alpine` | `clickhouse/clickhouse-server:24.8` |
| `mem_limit` | **256m**（`mem_reservation: 128m`） | 无 |
| 自定义配置挂载 | **有**（`deploy/clickhouse/keeper_and_cluster.xml`：Integrated Keeper + 单节点 cluster + macros） | 无 |
| `CLICKHOUSE_CLUSTER_ENABLED` | **未设** ⇒ 应用默认 `true`（集群路径） | **显式 `"false"`**（`docker-compose.yml:934`） |
| Keeper | 需要（由自定义配置提供） | 不需要 |
| 结论 | 更重、更高内存风险 | 官方 docker-compose 同款形态 |

来源：【项】两项目 `docker-compose.yml`、`deploy/clickhouse/keeper_and_cluster.xml`。

---

## 四、项目 A 专项评估

### 4.1 现状能跑起来吗？

| 判断 | 说明 |
|------|------|
| 功能自洽性 | ✅ 自带的 Keeper（tcp 9181）+ `remote_servers.default` + macros 恰好满足集群模式需求，`ON CLUSTER default` 迁移可执行，**这是它能跑通的原因**【项】。 |
| 内存合规性 | ❌ `256m` 使 ClickHouse 硬上限 ≈230 MB【推，§3.2】，**低于官方任何口径**（开发「远小于 4GB」的模糊下限、生产 8GB 下限、Langfuse 整机 16GiB 建议）。 |
| 瓶颈定位 | ① ClickHouse server 基础运行 + ② 集成 Keeper 元数据常驻内存 + ③ Langfuse 分析查询（去重/JOIN/聚合）三者叠加在同一 230 MB 预算内。哪怕低流量，后台 merge、迁移 DDL、dashboard 查询都可能触发「查询中止」或 OOM-kill。 |
| 与项目 B 差距 | 项目 B 无内存上限、无 Keeper、走非复制路径——同等硬件下明显更轻。 |

### 4.2 256 MB 够不够？

**不够。** 依据：官方非平凡查询建议 ≥4 GB，生产低数据量不低 8 GB；本项目上限约 230 MB，差 1–2 个数量级。官方无「256 MB 可用」档位说明。

### 4.3 最小可行内存建议（附推算）

| 档位 | 建议值 | 依据 |
|------|-------|------|
| 官方生产下限（ClickHouse server） | **≥4 GB** | 【官】Install / Requirements |
| 官方低数据量生产下限 | ≥8 GB | 【官】Sizing guide |
| 本项目（测试机、低流量、`:3`、开发/演示定位）务实值 | **≥2 GB**（能到 4 GB 更稳） | 【推】基于 4GB 官方下限向下折衷；Windows/WSL 测试机资源受限（见 memory：测试机 3.8G WSL） |

**无需 Keeper 的额外预算**：改用 `CLICKHOUSE_CLUSTER_ENABLED=false` 并去掉 Keeper 后，内存预算只需覆盖 ClickHouse server 本身，可用空间显著增加【推，§3.4】。

**整机视角提醒**：Langfuse v3 自托管官方建议整机 ≥16 GiB，本项目测试机仅 3.8 GB 且还需与 app/MySQL/Redis/MinIO 共存——**Langfuse profile 在日常开发中保持不启动是合理默认**；若必须启动，应作为独立资源窗口处理，并接受 ClickHouse 是其中最大内存消费者。

### 4.4 结论：该不该去掉 Keeper？（明确回答）

**该去掉，并同时显式设 `CLICKHOUSE_CLUSTER_ENABLED=false`。**

| 收益 | 代价 / 注意事项 |
|------|----------------|
| ① 去掉 Keeper 进程/元数据内存占用，256→可用空间更多；② 与官方 docker-compose 同款（官方单机默认 `false`）；③ 官方声明对性能与高可用无影响（单节点本无副本）；④ 少一份自定义配置维护 | ① **不会自动转换已有表**：若 `clickhouse_data` 卷里已建了 Replicated 表，改开关后旧表仍依赖 Keeper，必须**重建库/清卷**或做数据迁移（属破坏性操作，需确认后执行）；② 失去的只是多节点复制能力——当前单节点用不上；③ 仅改 env 而不移除配置文件挂载的话，Keeper 仍会随配置启动，**两处都要改**。 |

**落地动作（不在本次执行范围，供决策）**：
1. 在 `&langfuse-env` 锚点（`docker-compose.yml` 的 `langfuse-worker.environment`）加 `CLICKHOUSE_CLUSTER_ENABLED: "false"`，web 经 `<<: *langfuse-env` 自动继承。
2. 移除 `clickhouse` 服务的 `./deploy/clickhouse/keeper_and_cluster.xml` 挂载（并删除该文件）。
3. `mem_limit` 提到 `2g` 起（建议 `4g`），`mem_reservation` 同步上调。
4. 若卷内已有数据/表：确认无保留价值后 `docker compose --profile langfuse down -v`（或单独清 `corporate_rag_clickhouse_data` 卷）再启，确保走非复制迁移。

---

## 五、未核实项

| # | 未核实内容 | 查了什么 | 说明 |
|---|-----------|---------|------|
| 1 | ClickHouse 在 256 MB 下的**具体**启动失败/OOM 阈值 | ClickHouse Install/Requirements、Sizing、容器化部署社区文 | 官方仅给 4 GB 建议与「可远低于」定性说法，无阈值数字；社区文属【二】，未采信为结论。未做实测。 |
| 2 | 集成 Keeper 相对纯 server 的**精确**额外内存（MB） | ClickHouse Keeper 文档、Infrastructure Requirements、handbook | 官方只说「Keeper 全量数据集常驻内存」并给企业 pod 建议 8/16 Gi，**无集成模式的小部署数字**。 |
| 3 | 本项目所用 `24.12-alpine` 镜像内 `max_server_memory_usage_to_ram_ratio` 的实际取值 | 官方参数文档；未拉取镜像内 `config.xml` | 官方通用默认 `0.9`；镜像是否覆写未验证。 |
| 4 | v2→v3 的同口径性能基准（吞吐/延迟倍数） | v3 stable release、infrastructure-evolution 博客、v4 changelog | **官方无 A/B 基准数字**；仅有量级与目标值。 |
| 5 | `CLICKHOUSE_MIGRATION_CLUSTER_ENABLED` 是否仍存在 | Langfuse configuration 页、ClickHouse(自托管) 页、官方 docker-compose | 现行官方**配置文档与 docker-compose 只用 `CLICKHOUSE_CLUSTER_ENABLED`**；但官方 handbook 内提到 `CLICKHOUSE_MIGRATION_CLUSTER_ENABLED`，命名不一致，未在配置文档中查到该变量。**以 `CLICKHOUSE_CLUSTER_ENABLED` 为准**。 |
| 6 | 本项目改 `false` 后是否与现有 langfuse-web/worker 版本完全兼容 | 官方 v3 文档 | 官方明确支持且为单机推荐，但未在本机实测。 |

---

## 六、来源清单

**Langfuse 官方**
- v3 stable release：https://langfuse.com/changelog/2024-12-09-Langfuse-v3-stable-release
- 架构演进博客：https://langfuse.com/blog/2024-12-langfuse-v3-infrastructure-evolution
- 3.0 架构预告 discussion #1902：https://github.com/orgs/langfuse/discussions/1902
- ClickHouse(自托管)：https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse
- 环境变量配置：https://langfuse.com/self-hosting/configuration
- docker-compose 部署指南：https://langfuse.com/self-hosting/deployment/docker-compose
- v2 自托管页（生命周期）：https://langfuse.com/self-hosting/v2
- handbook·ClickHouse（集群/单实例迁移）：https://langfuse.com/handbook/product-engineering/infrastructure/clickhouse
- v4 changelog（v3 补丁截止 2027-01）：https://langfuse.com/changelog/2026-08-17-langfuse-v4
- 官方 docker-compose.yml：https://github.com/langfuse/langfuse/blob/main/docker-compose.yml

**ClickHouse 官方**
- Install / Requirements（4GB 建议）：https://clickhouse.com/docs/install ；https://github.com/ClickHouse/ClickHouse/blob/master/docs/en/getting-started/install.md
- Sizing and hardware recommendations（8GB 下限、4GB:1）：https://clickhouse.com/docs/guides/oss/best-practices/sizing-and-hardware-recommendations
- max_server_memory_usage_*（0.9、cgroup 感知）：https://clickhouse.com/docs/reference/settings/server-settings/settings/max-server-memory-usage
- Keeper 内存（Infrastructure Requirements）：https://clickhouse.com/docs/cloud/clickhouse-private/reference/infrastructure-requirements

**项目一手事实**
- `corporate_rag/docker-compose.yml`（clickhouse 服务、langfuse env 锚点）
- `corporate_rag/deploy/clickhouse/keeper_and_cluster.xml`
- `WeKnora/docker-compose.yml:832-960`（clickhouse 服务、`CLICKHOUSE_CLUSTER_ENABLED: "false"`）
