## ADDED Requirements

### Requirement: 部署流程的唯一步骤来源

仓库 SHALL 只在 `docs/agents/deploy-runbook.md` 一处承载发布/部署的**操作步骤**；其余文档（`README.md`、`docs/agents/cookbook.md` 等）SHALL 只链接该文档，不得复制其步骤正文。

#### Scenario: 查阅发布步骤

- **WHEN** 需要执行或复核一次发布
- **THEN** 操作步骤只在 `docs/agents/deploy-runbook.md` 中，其他文档以指针形式引用它

### Requirement: 资源档位以 dev 配置为基准

runbook SHALL 规定 2C8G 单机目标的资源档位取自 `docker-compose.yml`（dev）的 `mem_limit`（合计约 3 GB），并 SHALL 明确 `docker-compose.prod.yml` 的限额（合计 14.25 GB）**不适用**于该目标机。

#### Scenario: 选择资源档位

- **WHEN** 在 2C8G 机器上配置容器限额
- **THEN** 采用 dev compose 的档位，且 runbook 写明 prod 档位会因超出物理内存而触发 OOM

#### Scenario: 说明资源富余度

- **WHEN** 读者评估 2C8G 是否够用
- **THEN** runbook 给出实测占用（整栈 < 1 GB）与峰值所在（构建镜像、PDF 解析、langfuse-web 贴近限额）

### Requirement: 内部服务端口不对外且密钥不依赖弱默认

runbook SHALL 规定 redis / postgres / app / minio 的宿主端口只绑回环或由安全组封闭，仅入口端口对外开放；SHALL 规定 `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` 与应用的 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` **两对键必须成对相等**，且密钥不得依赖 compose 的 `${X:-默认值}` 兜底。

#### Scenario: 核对对外暴露面

- **WHEN** 部署完成后检查对外可达端口
- **THEN** 只有入口端口可达，redis / app / minio 等宿主端口不可从外部访问

#### Scenario: 配置对象存储凭据

- **WHEN** 设置 MinIO 凭据
- **THEN** 两对键被设为相同值；runbook 指出只改其中一对会导致应用连不上存储

### Requirement: 数据迁移有明确执行位置与顺序

runbook SHALL 规定数据库迁移（`alembic upgrade head`）的**执行位置**（宿主，或把 `alembic/` 与 `alembic.ini` 挂进容器）与**执行顺序**（在冒烟之前），并 SHALL 说明运行时镜像内不含 `alembic/` 与 `alembic.ini`、应用不自动建表。

#### Scenario: 首次上机的迁移

- **WHEN** 首次在目标机拉起服务
- **THEN** 在冒烟之前执行迁移，runbook 给出可复制的命令

#### Scenario: 试图在容器内直接跑迁移

- **WHEN** 不挂载 `alembic/` 与 `alembic.ini` 就执行迁移
- **THEN** runbook 已预告该路径不成立（镜像内无这两个路径），避免读者误判为环境故障

### Requirement: 发布制品为已提交的 commit 且工作区干净

runbook SHALL 规定发布以**已提交的 commit** 为制品，并要求发布前置检查工作区干净（`git status` 为空）。

#### Scenario: 发布前置检查

- **WHEN** 开始一次发布
- **THEN** 先确认工作区干净，未提交的改动不得进入发布

### Requirement: 冒烟清单可执行且作为发布成功判据

runbook SHALL 提供最小冒烟清单（健康检查 → 登录 → 上传一份文档 → 等待就绪 → 提问并看到引用），并 SHALL 规定缺任一步不得判定发布成功。

#### Scenario: 判定发布成功

- **WHEN** 部署动作执行完毕
- **THEN** 逐项跑通冒烟清单才算发布成功，任一步失败即视为发布未完成

### Requirement: 回滚与备份口径

runbook SHALL 规定代码回滚方式（回退到上一个 commit 并同步/重启），SHALL 规定数据侧备份要求，并 SHALL 指出 MinIO 中的原始上传文件是**唯一不可再生的源头**。

#### Scenario: 执行回滚

- **WHEN** 发布后需要回滚
- **THEN** 按 runbook 的代码回滚步骤执行，并明确数据侧是否需要依赖备份

#### Scenario: 识别备份缺口

- **WHEN** 评审部署就绪度
- **THEN** runbook 已写明哪些数据必须备份、以及不备份的后果

### Requirement: 未决项显式登记而非以已决定口吻出现

本变更未决的项（HTTPS 选型、Langfuse 去留、`docker-compose.prod.yml` 与 dev 档位对齐、CI/CD、托管化）SHALL 登记在 `docs/agents/requirements_pool.md`；runbook SHALL NOT 以"已决定"的口吻描述这些项。

#### Scenario: 阅读 runbook 时区分已定与未定

- **WHEN** 读者查看 HTTPS 或 Langfuse 相关段落
- **THEN** 能明确看出哪些是当前采用口径、哪些仍是待定项
