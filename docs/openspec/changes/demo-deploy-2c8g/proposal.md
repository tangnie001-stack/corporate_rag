## Why

项目**从未发布过**：代码只在开发机（WSL）上跑，仓库里所有叫"部署"的内容（`README.md` 部署指南、`cookbook.md` 部署节）都是"在本机把栈起起来"，`docker-compose.prod.yml` 自述"本轮 prod 不部署"、从未在目标机验证。因此一批**只在真实交付时才暴露**的缺口长期无人处理，且没有一个环节逼它们被解决：

- `MINIO_ROOT_USER/PASSWORD` 与应用的 `MINIO_ACCESS_KEY/SECRET_KEY` 是两对键，`.env.example` 只列了后者 → prod compose 直接校验失败退出
- nginx 未设 `client_max_body_size`（默认 1m），而应用允许 10MB → 真实文档上传必 413
- 应用不自动建表（`create_all` 零命中），且镜像里**没有** `alembic/` 与 `alembic.ini` → 迁移没有执行点
- prod compose 的 `mem_limit` 合计 **14.25 GB**，在 2C8G 目标机上必被 OOM

现在目标明确：阿里云 **2C8G 单机**、对外 demo。需要先把"发布/部署流程"写下来作为**唯一归属**，后续配置改动才有依据。

## What Changes

- **新增归属文档 `docs/agents/deploy-runbook.md`**：单机发布流程的唯一步骤来源，按六段组织 —— 目标形态 / 首次上机 / 数据迁移 / 冒烟 / 日常更新 / 回滚与备份（含安全组与密钥口径）
- **在文档里固化已实测的口径**（不再散落对话）：
  - 资源以 **dev 档位**为基准（实测整栈 <1 GB；prod 档位 14.25 GB 不可用于 2C8G）
  - **内部端口收回回环**（dev 把 redis 6379 / app 8000 / minio 9000 发布到 0.0.0.0）
  - **MinIO 两对键必须成对相等**；密钥不得依赖 compose 的弱默认值
  - **迁移的执行位置**（宿主或把 `alembic/` + `alembic.ini` 挂进容器）
  - **先提交、再发布**（工作区干净才发）
- **登记本次不做的项**进 `docs/agents/requirements_pool.md`：HTTPS 选型、Langfuse 去留、`docker-compose.prod.yml` 与 dev 档位对齐、CI/CD、托管化（RDS/Redis/OSS/SLB）
- **`CLAUDE.md` 文档组织表登记该归属文档**（一事一档要求）
- **本 change 只落文档**：不改任何 compose / nginx / `.env`；线性的配置改动另开 change

## Capabilities

### New Capabilities

- `deployment-runbook`: 单机对外 demo 的发布/部署流程文档——必须覆盖的环节（目标形态、上机前置、迁移执行位置、冒烟清单、日常更新、回滚、备份、端口与密钥约束），以及文档的唯一归属性

### Modified Capabilities

（无 —— 本 change 不改任何既有能力的规格级行为）

## Impact

- **新增**：`docs/agents/deploy-runbook.md`（归属文档）
- **修改**：`CLAUDE.md`（文档组织表加一行）、`docs/agents/requirements_pool.md`（登记本次不做的项）
- **不改**：任何代码、`docker-compose*.yml`、`deploy/nginx/`、`.env`
- **无影响**：API 契约、数据库结构、测试断言、前端取值
