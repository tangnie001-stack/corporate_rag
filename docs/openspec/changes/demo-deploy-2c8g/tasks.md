> 本变更**只落文档**：不改任何 `docker-compose*.yml` / `deploy/nginx/` / `.env`。配置改动在 §5.2 登记后另开 change。

## 1. 归属文档骨架

- [ ] 1.1 新建 `docs/agents/deploy-runbook.md`，按六段建骨架：目标形态 / 首次上机 / 数据迁移 / 冒烟 / 日常更新 / 回滚与备份
- [ ] 1.2 文件头写明归属与不复制原则：本文件是**发布/部署步骤的唯一归属**，`README.md`、`cookbook.md` 等只链接不复述（对应 spec「部署流程的唯一步骤来源」）
- [ ] 1.3 「目标形态」段：单机 2C8G 对外 demo；采用 `docker-compose.yml`（dev）档位（合计 ≈3 GB）；写明实测整栈占用 <1 GB 与峰值所在；写明 prod compose 的 14.25 GB 档位不适用（对应 spec「资源档位以 dev 配置为基准」）
- [ ] 1.4 「端口与密钥」段：内部端口（redis / postgres / app / minio）只绑回环或由安全组封闭，仅入口端口对外；`MINIO_ROOT_USER/PASSWORD` 与 `MINIO_ACCESS_KEY/SECRET_KEY` **成对相等**；不得依赖 compose 的 `${X:-默认值}`（对应 spec「内部服务端口不对外且密钥不依赖弱默认」）

## 2. 首次上机

- [ ] 2.1 「目标机前置」：装 Docker、克隆仓库、放 `.env`、安全组只开 22 + 入口端口、加 2–4 GB swap、系统盘 ≥60 GB（build cache 实测可达 6.9 GB）
- [ ] 2.2 「拉起服务」：给出可复制命令，并写明显式指定 compose 文件时 `docker-compose.override.yml` 是否参与（让读者可用 `docker compose config` 自查）
- [ ] 2.3 「前置必改项」：列出 nginx `client_max_body_size`、MinIO 两对键、alembic 挂载三项，各带判据；明确标注**这三项属于另一个 change 的范围**，本变更不改
- [ ] 2.4 「数据迁移」段：给出两条路（宿主执行 / 把 `alembic/` 与 `alembic.ini` 挂进 app 容器）+ 推荐路线 + **顺序必须在冒烟之前**；说明镜像内不含这两个路径、应用 `create_all` 零命中不自动建表（对应 spec「数据迁移有明确执行位置与顺序」）

## 3. 冒烟与日常更新

- [ ] 3.1 「冒烟清单」：`/api/health` → 登录 → 上传一份文档 → 等待 `ready` → 提问并看到引用；写明缺任一步不得判定发布成功（对应 spec「冒烟清单可执行且作为发布成功判据」）
- [ ] 3.2 「日常更新」四种情形各自的命令：① 只改代码（同步 `src/` + restart）② 依赖或 Dockerfile 变（build + force-recreate）③ 配置变（force-recreate）④ 迁移变（升级命令）
- [ ] 3.3 「发布前置」：制品 = 已提交的 commit；发布前 `git status` 必须为空，未提交改动不得进入发布（对应 spec「发布制品为已提交的 commit 且工作区干净」）

## 4. 回滚与备份

- [ ] 4.1 「代码回滚」：回退到上一个 commit 并同步/重启的具体步骤
- [ ] 4.2 「备份」：需要备份的数据、频率与位置（待 Open Question 定稿后填实）；写明 **MinIO 中的原始上传文件是唯一不可再生的源头**（对应 spec「回滚与备份口径」）

## 5. 登记与同步

- [ ] 5.1 `CLAUDE.md` 文档组织表新增一行：`docs/agents/deploy-runbook.md` —— 归属内容「发布/部署流程的唯一步骤来源」，何时查阅「执行发布、排查部署问题前」
- [ ] 5.2 `docs/agents/requirements_pool.md` 新增登记项：① HTTPS 选型 ② Langfuse 去留 ③ `docker-compose.prod.yml` 与 dev 档位对齐 ④ CI/CD 与镜像仓库 ⑤ 托管化（RDS/Redis/OSS/SLB）⑥ §2.3 三项前置必改项对应的配置 change
- [ ] 5.3 复核 runbook 全文措辞：待定项用「当前口径 / 待定」表述，不得写成已决定（对应 spec「未决项显式登记而非以已决定口吻出现」）

## 6. 闸门与验收

- [ ] 6.1 跑 `python -m src.cli.check_docs`，0 error（doc anti-rot 闸门）
- [ ] 6.2 跑 `openspec validate demo-deploy-2c8g --strict` 通过
- [ ] 6.3 可追溯性核对：spec 的 8 条 requirement 各自在 runbook 中有落点，无悬空、无多余
- [ ] 6.4 按 `CLAUDE.md`「验证」清单自检：一事一档（新归属文档已登记进表）、无复制其他文档正文、无新增术语遗漏 glossary
