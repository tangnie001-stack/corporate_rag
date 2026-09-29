## Context

项目从 2026-08 起只在开发机（WSL / ext4）上运行，**从未发布过**。仓库里叫"部署"的内容有两类，都不是发布流程：

| 位置 | 实际是什么 |
|---|---|
| `README.md` §部署指南 | 本地启动说明（`docker compose up -d --build`） |
| `docs/agents/cookbook.md` §部署 | 本地容器操作（改 `.py` 后 restart、ALTER TABLE） |
| `docker-compose.prod.yml` | 一机全栈配置，文件头自述"本轮 prod 不部署"，从未在目标机验证 |

现状约束（均已实测，不是推测）：

- 目标机：阿里云 **2C8G 单机**，对外 demo
- `docker-compose.prod.yml` 的 `mem_limit` 合计 **14.25 GB**；`docker-compose.yml`（dev）合计 **3.00 GB**（去掉 litellm profile 后 2.25 GB）
- 实测整栈占用 **≈715 MiB**（app 254 / langfuse-web 212 / minio 144 / postgres 83 / nginx 12 / redis 10），CPU 近 0（推理全在 DashScope 远端）
- `docker-compose.prod.yml:71-72` 要求 `MINIO_ROOT_USER/PASSWORD`，而 `.env.example` 只列应用的 `MINIO_ACCESS_KEY/SECRET_KEY` → **prod compose 会在校验阶段直接退出**
- `Dockerfile` 只 `COPY src/ scripts/ deploy/` → 镜像内**没有** `alembic/` 与 `alembic.ini`（但 `alembic` CLI 在 `/usr/local/bin`，因为装了依赖）；应用 `create_all` 零命中，**不自动建表**
- `deploy/nginx/nginx.conf` 只 `listen 80`，无 `client_max_body_size`（默认 1m，应用侧允许 10MB），无 443 server block，`deploy/nginx/ssl/` 目录不存在
- `docker-compose.override.yml` 会被 `docker compose` **自动合并**，给 app 挂 `./src`、`./tests`，给 nginx 挂宿主 conf/html
- 部署纪律为零：`git tag` 0 个、无 `CHANGELOG`、无 CI、`scripts/` 无部署脚本、`pyproject.toml` 版本恒 `0.1.0`

## Goals / Non-Goals

**Goals:**

- 让"发布/部署流程"第一次有**唯一归属文档**，六段齐全：目标形态 / 首次上机 / 数据迁移 / 冒烟 / 日常更新 / 回滚与备份
- 把已在对话与实测中确认的口径**固化进文档**（资源档位、端口、MinIO 键对、迁移位置、制品形态），不再依赖会话记忆
- 明确区分"当前采用口径"与"仍待定项"，待定项登记进需求池

**Non-Goals:**

- **不改任何 compose / nginx / `.env`**（本 change 只落文档；配置改动另开 change，便于独立评审）
- 不引入 CI/CD、镜像仓库、监控告警、托管化（RDS/Redis/OSS/SLB）
- 不为 2 机 + SLB 的横向扩展做设计（与"流式状态在进程内、单 worker"的既定形态冲突，属独立问题）
- 不动 `docker-compose.prod.yml`

## Decisions

### D1 目标形态 = 单机 2C8G 跑 dev compose，prod compose 不动

**理由**：dev 的 `mem_limit` 档位是本机 8 GB 上**实测跑过**的（整栈实测 <1 GB），prod 的 14.25 GB 在 2C8G 上必 OOM 且从未验证；两者容器名/卷名/project name 完全相同，改 prod 只会引入需要重新验证的面。用户明确"不用 prod、prod 不动"。

**替代方案**：① 把 prod compose 的限额对齐 dev —— 收益小（还是同一个栈）、要重验；② 直接托管化 —— 与"能用托管就用托管"的原则一致，但属另一个数量级的工程，且当前唯一硬需求是"对外 demo 能看"。

**已知代价**：`docker-compose.prod.yml` 会长期处于"不可用且未标注"状态。缓解见 Risks。

### D2 本 change 只落文档

**理由**：用户明确"部署的内容，可以先落文档"。文档先行还有一个实际好处——配置改动可以被文档反过来约束（先定口径，再改配置），避免边改边发现口径不一致。配置项（`docker-compose.demo.yml`、nginx 上传上限、MinIO 键对、alembic 挂载）在需求池登记，另开 change。

### D3 HTTPS：当前口径 = demo 先纯 HTTP；选型留待定

**理由**：单机 demo 的最短路径；改成 443 需要引入证书来源与续期，属额外决策面。文档写清"非生产口径"，并把 443 段落标为**可选**。

**替代方案**：① nginx 直接上 443（自签或 Let's Encrypt）——单机最自洽，但要定证书来源；② SLB 终止 TLS —— 与"单机"取向相左，多一个组件。两者都进 Open Questions。

### D4 Langfuse：当前口径 = 关掉；去留留待定

**理由**：两个实测依据 —— ① 它默认绑 `127.0.0.1:3000`，**对外 demo 本来就看不到**；② 内存实测 `211.8 MiB / 256 MiB` = **82.7%**，稍有并发即被 OOM 杀。关掉可省 **1.12 GB 镜像 + 约 0.5 GB 内存**与一整套密钥（H-02/H-03 也随之下线）。

**若将来保留**：必须把限额提到 512m 并加 nginx server block + 证书。

### D5 迁移的执行位置：宿主或挂载进容器，二选一写进文档

**理由**：镜像内无 `alembic/` 与 `alembic.ini`，`docker compose exec app alembic upgrade head` 在现状下**不成立**。两条可行路：① 宿主执行（dev compose 已发布 `127.0.0.1:5432`，本就为此）——需要在目标机有 Python 环境；② 把 `./alembic` 与 `./alembic.ini` 挂进 app 容器——无需宿主环境，且与"配置改动另开 change"相容。文档给两条，标注推荐。

**顺序约束**：迁移必须在冒烟**之前**（`0001_pg_baseline` 建 8 张表，`0002_kb_domain` 加列）。

### D6 制品形态 = 已提交的 commit（+ 同步 `src/`），镜像低频重建

**理由**：override 把 `./src` 挂进容器，所以日常发布的"制品"实际是 **commit + 文件同步 + `restart app`**；镜像只在 `pyproject.toml` / `Dockerfile` 变动时才需要 `build`。这条把 2C8G 上最贵的一步（构建）降为低频操作。

**代价**：目标机必须存在仓库工作副本（不是"只放镜像"）。对 demo 可接受。

### D7 文档归属 = 新建 `docs/agents/deploy-runbook.md`，并在 `CLAUDE.md` 文档组织表登记

**理由**：`CLAUDE.md` 的「一事一档」要求每条事实只有一个归属文档、新归属文档必须同步登记进表，否则视为未完成。

### D8 冒烟清单（5 步）作为发布成功判据

健康检查 `/api/health` → 登录 → 上传一份文档 → 等待 `ready` → 提问并看到引用。缺任一步不算发布成功。**理由**：现有验证资产（pytest / ruff / pyright / doc 闸门）覆盖的是开发侧，交付侧一条都没有；这 5 步是能真实暴露"上传被 nginx 挡""表未建""存储凭据不匹配"的最小集合。

## Risks / Trade-offs

- **[文档先行、配置未改 → 照文档执行会撞已知缺口]** → runbook 把这几个缺口标为"**前置必改项**"并给出判据（如 `client_max_body_size`、MinIO 两对键），同时在需求池登记对应 change。读者不会误以为现状可直接发布。
- **[`docker-compose.prod.yml` 长期是"不可用且未标注"的配置]** → 在需求池显式登记"prod compose 与 dev 档位对齐"为待定项；在本文档说明它不参与本目标形态，避免后来者误用它。
- **[2C8G 上构建镜像是三峰同时（CPU/磁盘/内存）]** → runbook 要求加 2–4 GB swap；构建后 `docker builder prune -f`；磁盘建议 ≥60 GB（build cache 实测可达 6.9 GB）。
- **[dev compose 的密钥走 `${X:-弱默认值}`，缺值也能起来]** → runbook 要求显式设值、不得依赖兜底；并把"MinIO 两对键成对相等"单列一条。
- **[单机是单点：PG 与 MinIO 同机]** → 接受（demo 口径）；用备份缓解，并写明 MinIO 原始文件是**唯一不可再生源**。
- **[worktree 只隔离 git、不隔离 Docker]** → 与本目标形态无关（不在 worktree 内部署）；仅在生产机单副本上操作。

## Migration Plan

1. 本 change 落文档（`deploy-runbook.md` + `CLAUDE.md` 登记 + 需求池登记）
2. 按 runbook 执行首次上机（**待**：HTTPS/Langfuse 两个口径定稿 + 前置必改项对应的 change 落地）
3. **回滚**：本 change 是纯文档，回滚 = `git revert` 对应提交；无数据面与契约面回滚

## Open Questions

1. **HTTPS**：纯 HTTP（当前口径）／nginx 443 + 证书（自签 or Let's Encrypt）／SLB 终止 TLS？
2. **Langfuse**：关掉（当前口径）／保留（则需 nginx 暴露 3000 + 证书 + 限额提到 512m）？
3. **配置改动落在哪**：新建 `docker-compose.demo.yml` 覆盖，还是直接改 `docker-compose.prod.yml` 对齐 dev 档位？
4. **域名与证书来源**：有无域名？证书从哪来？
5. **首次语料导入**：demo 展示哪批文档、由谁导入、走界面上传还是灌库？
6. **备份口径**：频率、保留期、存放位置（是否上 OSS）？
