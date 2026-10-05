# 部署 Runbook（单机对外 demo）

> **归属**：本文件是**发布 / 部署步骤的唯一归属**。`README.md`、`docs/agents/cookbook.md`、各 compose 文件注释等一律**只链接本文件**，不复述步骤正文。
>
> 适用目标：阿里云 ECS **单机（2C8G）对外 demo**。运行形态见 §1。
> 相关：发布链路与流水线配置见 §2；首次上机 §3；迁移 §4；冒烟 §5；日常更新 §6；回滚与备份 §7；仍未决定的口径见文末「待定项」。

---

## 1. 目标形态

- **运行形态**：目标机只运行 `docker-compose.image.yml` —— **全部镜像来自 ACR，目标机不构建**。
- **与 dev / prod compose 的关系**：
  - `docker-compose.yml`（dev）面向开发机，**不在目标机使用**；
  - `docker-compose.prod.yml` 从未在目标机验证、且档位会 OOM，**不在本目标形态内**（其去留见文末待定项）。

### 1.1 服务与镜像来源

| 服务 | 镜像（ACR） | 库 |
|---|---|---|
| redis | `deploy_base:redis-7.4.9-alpine` | 公开 |
| postgres（pgvector） | `deploy_base:pgvector-pg15.19-0.8.6` | 公开 |
| minio | `deploy_base:minio-2026-09-21` | 公开 |
| langfuse-web | `deploy_base:langfuse-2.95.11` | 公开 |
| nginx | `deploy_base:nginx-1.31.6-alpine` | 公开 |
| app | `deploy_store_local:<构建号>` | **私有** |

- **公开库 `deploy_base` 免登录**；**私有库 `deploy_store_local` 需先 `docker login`**（app 镜像内含 `src/ scripts/ skills/ agents/ alembic/`，故不进公开库）。
- tag 一律带 patch 号（升级基镜像要改引用、走评审，避免"同 tag 覆盖"静默换基）。

### 1.2 资源档位

沿用 **dev 档位**（`docker-compose.image.yml` 的 `mem_limit` 合计 **≈2.28 GB**：redis 128m / postgres 1g / minio 256m / langfuse-web 256m / nginx 128m / app 512m）。

实测整栈占用 **≈715 MiB**（app 254 / langfuse-web 212 / minio 144 / postgres 83 / nginx 12 / redis 10）→ **2C8G 有富余**。

> ⚠️ `docker-compose.prod.yml` 的档位合计 **14.25 GB**，在 2C8G 上**必被 OOM**，不要用它。
> 构建镜像已移到流水线构建机，目标机不再承担构建的三峰压力。

### 1.3 端口与安全组

- **对外只开 nginx 的 80**；安全组**只放行 22 与 80**。
- 其余端口一律绑回环或由安全组封闭：app `127.0.0.1:8000`、redis / postgres / minio / langfuse-web 均不对外。
- 冒烟与健康检查走 `http://127.0.0.1/api/health`（经 nginx:80 → app:8000），与对外路径一致。

### 1.4 密钥与挂载路径

- 密钥**只放** `/opt/wwww/corporate_rag/.env`（**人工放置**，`chmod 600`）；**制品不含 `.env`**。
- 不得依赖 compose 的 `${X:-默认值}` 兜底 —— `deploy.sh` 会按 compose 插值变量清单校验非空，缺键即失败。
- MinIO 两对键**已同源**：`docker-compose.image.yml` 直接用 `.env` 的 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` 供 `MINIO_ROOT_USER/PASSWORD`。**不要**改回需要人工保持相等的两对。
- 目标机必须存在的路径（缺任一条对应功能失效）：`.env`、`deploy/nginx/nginx.conf`、`deploy/nginx/html/`、`deploy/postgres/init/`、`data/ragas/`。

---

## 2. 发布链路与流水线配置（云效）

### 2.0 发布全流程（端到端）

**默认发布分支：`dev-wsl`。** 一次发布按下面顺序走（1–2 在开发机，其余在云效网页）：

| # | 步骤 | 在哪 | 入口 / 命令 |
|---|---|---|---|
| 1 | **前置检查**：工作区干净 + 本地 `dev-wsl` 已推送、未落后 | 开发机 | `git fetch && git status -sb`（**说"开始部署"时由助手代查**）|
| 2 | **推代码** | 开发机 | `git push origin dev-wsl` |
| 3 | **GitHub → Codeup 同步代码**（当前**手动**）| 云效网页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/codeup/deploytest/tangnie001-stack/corporate_rag/settings/mirror_sync` |
| 4 | **流水线自动开始**（Codeup 有提交 → 钩子触发：构建推镜像 → 打包制品 → **生成部署单**）| 云效网页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/flow/my?page=1` |
| 5 | **创建部署单**（点「创建部署单」→ 跳部署页）| 云效网页 | 同上（流水线页）|
| 6 | **主机部署自动执行**（四槽位 `preflight → start → health_check → clean`）| 目标机 | 自动；日志在部署页 |
| 7 | **冒烟**（见 §5；缺任一步不算成功）| 浏览器 | `http://<ECS-IP>/` |
| 8 | **查看版本 / 回滚** | 云效网页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/appstack/app/deploy-app/versions` |

> **第 1 步可以交给助手**：只要说「开始部署」，助手会先 `git fetch` 并比对本地 `dev-wsl` 与 `origin/dev-wsl`，确认没有未推送 / 落后的提交（并检查工作区是否干净）再往下走。

#### 2.0.1 代码从 GitHub 到 Codeup（两条路，只留一条）

流水线**代码源是 Codeup**，而开发机只推 **GitHub** ⇒ 中间必须把提交搬到 Codeup：

1. **手动「镜像同步」（当前口径）**：到 §2.0 第 3 步的同步设置页点「立即同步」。零维护，代价是每次发布多一步人工。
2. **自动化（未实施）**：在 **GitHub Actions** 里 `git push --mirror` 到 Codeup（GitHub 侧网络好、几秒），Codeup 的 push 事件再触发流水线。

⚠️ **两条路只应保留一条** —— Codeup 的镜像同步是**强制覆盖**；若既手动镜像同步、又让本地/Actions 直推 Codeup，两边会互相打架。直接"一次推两边"（`git remote set-url --add --push`）同理，且 Codeup 需另配凭据，不如走第 2 条。

### 2.1 发布链路：流水线产出「部署单」，部署页面触发发布

```
Git 提交 ──▶ ① 构建并推送 app 镜像 ──▶ ② 打包部署制品（tgz）──▶ ③ 生成部署单
              [云效构建机]              [云效构建机]              [云效]
                                                                  │
                            ④ 主机部署 ◀── 人工：部署页面点「创建部署单」
                               [目标机]
```

- ① 用 `DockerBuildPushACR` 步骤（**在云效构建机执行**）：`dockerTag = ${BUILD_NUMBER}`。
- ② 用 `scripts/ci/pack-deploy-artifact.sh`（**在云效构建机执行**，即**流水线打包阶段的执行命令**）：把**同一个 `${BUILD_NUMBER}`** 注入 `docker-compose.image.yml` 的 `image:`。
  ⚠️ **本脚本不在本地运行、也不在目标机运行** —— 它的运行位置就是流水线的打包阶段。本地仅在演练时手工跑，且必须显式 `--tag <tag>`（本地没有 `BUILD_NUMBER`，脚本会 `fail`）。
- ③ **流水线的最后一步是「生成部署单」** —— 它**不部署任何东西**，只产出一张待执行的部署单。
- ④ **真正的发布由人工触发**：在**部署页面**点「**创建部署单**」→ 跳转到部署页 → 执行主机部署（把制品下发到目标机、解压到 `/opt/wwww/corporate_rag`、依次跑 4 个槽位脚本，见 §2.4）。

> ⚠️ **正常流程 =「流水线走到生成部署单」+「人工在部署页面创建部署单发布」** —— 不要把主机部署改成流水线里自动跑。

> 各段执行机器：①② 在**云效构建机**（云效杭州集群），③ 在云效控制面（生成部署单，不碰机器），④ 在**目标机 ECS**。`deploy.sh` 只在第 ④ 段（目标机）跑。

> **代码源前置**：流水线代码源 = **Codeup**；GitHub 的提交需先**同步到 Codeup**（当前为手动「立即同步」）才会被本次运行取到 —— 这是 ① 与 ② 的**共同**前置，不是 ① 独有的。

> **`BUILD_NUMBER` 是平台内置变量，不是 ① 的产物**：它是**本次流水线运行序号**（云效内置、每次运行 +1），同一运行内**每个步骤都拿到同一个值**（① 段日志 `BUILD_NUMBER=4`、③ 段日志 `+ export BUILD_NUMBER=4` 即证）。所以 ① 的 `dockerTag: ${BUILD_NUMBER}` 与 ② 的 `TAG="$BUILD_NUMBER"` 结构性相同，**不需要 ① 先跑、也没有段间传递**。
> ⚠️ 唯一会让两者不一致的情形是 **① 与 ② 不在同一条流水线**（各有独立运行序号）—— 因此"**同一条流水线**"才是 tag 一致的前提。

> **依赖关系**：② 只读仓库文件 + `${BUILD_NUMBER}`，**不碰 docker / ACR / 镜像** ⇒ 与 ① **无依赖，二者可并行**。而 **④（部署单被执行时）需要 ① 和 ② 都已成功** —— 既需要 ② 产出的制品，也需要 ① 已把镜像推进 ACR（`deploy.sh` 第 4 步 `docker manifest inspect` 校验，取不到即 `fail`）。（①② 之间怎么编排不影响正确性：可并行，也可串行。）

> **tag 一致性**：因为 ① 与 ② 在同一条流水线、共享 `BUILD_NUMBER`，所以**制品里的 tag = 实际推送的镜像 tag**，不需要人工同步。
> 部署步骤的 `deploy.sh` 第 4 步会 `docker manifest inspect` 校验该镜像存在；取不到即说明"制品与镜像不配套"（多半是构建段失败）。

### 2.2 变量与缓存（「变量和缓存」）

| 变量 | 作用 | 注意 |
|---|---|---|
| `PIP_REPO_USER` | **制品仓库（云效 packages）认证**的用户名 | 供构建期 `pip install` 走 PyPI 代理仓；**不是 ACR 凭据** |
| `PIP_REPO_PASS` | 上述密码 | **勾「私密模式」** |
| （ACR 凭据） | 拉/推 ACR 镜像 | 构建侧走 serviceConnection；**目标机另需 `docker login`**（`ACR_USER`/`ACR_PASSWORD` 或手工登一次） |

❗ **两套凭据不可混用**：`PIP_REPO_*` 服务制品仓库（HTTP Basic），登不了 ACR（Registry v2 token 流）。

**新建/复制流水线时最容易漏的就是这组变量** —— 若构建期报"索引里查不到任何包"，先回来核对本节。

> ⚠️ `LLM_MODEL` 之类的流水线变量注入的是**目标机的环境变量**；而 **app 容器只读 `env_file: .env`**（compose 的 `environment:` 段没列 `LLM_MODEL`）。所以**改模型要改服务器上的 `.env` 并重跑部署**，改流水线变量对 app 无效。

### 2.3 构建步骤字段口径

| 字段 | 值 | 说明 |
|---|---|---|
| `dockerfilePath` | `Dockerfile` | 相对**项目根**；填 `/` 或带仓库名前缀都会报错 |
| `contextPath` | **留空** | 默认解析成项目根，别手动填 |
| `dockerTag` | `${BUILD_NUMBER}` | 与打包段同源 |
| `options` | `--platform linux/amd64 --build-arg PIP_REPO_USER --build-arg PIP_REPO_PASS` | **不带值**，从环境变量取 |

❗ `options` 字段**不展开 `${...}`** —— 写 `--build-arg PIP_REPO_USER=${PIP_REPO_USER}` 会把**字面量** `${PIP_REPO_USER}` 当用户名传给 pip（表现为"`Could not find a version ... (from versions: none)`"）。

### 2.4 主机部署四槽位（配在**部署页面**上）

这四个槽位**配在云效「部署」页面的主机部署里**（不是配在流水线里），由**人工在部署页面点「创建部署单」**后按顺序执行。制品由平台下发到目标机（路径以部署配置为准，常见 `/home/admin/app/package.tgz`），下面的启动槽位把它解压到标准发布路径 `/opt/wwww/corporate_rag`。

调用顺序：**停止 → 启动 → 健康检查 → 清理**。原则：**破坏性动作必须排在启动之后**。

**停止槽位（只做预检，不停服）**：
```bash
if [ -f /opt/wwww/corporate_rag/scripts/deploy/deploy.sh ]; then
  cd /opt/wwww/corporate_rag && bash scripts/deploy/deploy.sh preflight
else
  echo "[preflight] 首次部署：机上尚无 deploy.sh，跳过（无旧版本需保护）"
fi
```

**启动槽位**：
```bash
set -euo pipefail
ART=/home/admin/app/package.tgz
ROOT=/opt/wwww/corporate_rag
[ -f "$ART" ] || { echo "[start] 制品不存在: $ART"; exit 1; }
mkdir -p "$ROOT"
rm -rf "$ROOT/scripts"; rm -f "$ROOT/docker-compose.image.yml"   # 非挂载路径，可安全清
tar zxf "$ART" -C "$ROOT"                                        # deploy/ 不动（bind mount）
cd "$ROOT"
bash scripts/deploy/deploy.sh start
```

**健康检查槽位**：
```bash
cd /opt/wwww/corporate_rag && bash scripts/deploy/deploy.sh health_check
```

**清理槽位**：
```bash
cd /opt/wwww/corporate_rag && bash scripts/deploy/deploy.sh clean
```

> 为什么"停止"只做预检：若它真停服，之后任一步失败（制品损坏、拉镜像失败）会把服务留在**下线**状态。停旧起新由启动阶段的 `compose up -d` 一次完成，失败时旧版本保持在线。
> `deploy.sh` 支持的完整模式：`deploy|start`、`preflight`、`health_check`、`stop`、`clean`（`bash scripts/deploy/deploy.sh -h`）。

### 2.5 已踩坑清单（换流水线/重建时对照）

1. **`{{ }}` 会在云效侧被当占位符渲染** —— 主机部署脚本里禁用（`docker --format '{{.X}}'` 一律改 shell 解析）。
2. **`options` 字段不展开 `${...}`** —— 用不带值的 `--build-arg NAME`。
3. **Alinux 4 自带 `docker` 与 `docker-ce` 互斥** —— 只补 `docker-compose-plugin` 即通（别装 `docker-ce` 全家桶）。
4. **`docker compose restart` 不吃 `.env`** —— 改 `.env` 后要 `docker compose up -d --force-recreate app`。
5. **制品下载**：用云效内建「下载制品」；若自己 `curl`，务必 `-fL`（`-f` 让 HTTP 错误直接失败，避免把错误响应体当成包）。

---

## 3. 首次上机

1. **装 Docker + compose 插件**（Alinux 4：`dnf install -y docker-compose-plugin`）。
2. **ACR 登录**（app 镜像在私有库）：`docker login <ACR>` 一次（凭据落 `/root/.docker/config.json`），或用 `ACR_USER`/`ACR_PASSWORD`。
3. **放 `.env`**：从团队渠道取键清单，填真实值，`chmod 600 /opt/wwww/corporate_rag/.env`。
4. **安全组**：只放行 22 + 80。
5. **系统**：swap ≥2 GB、系统盘 ≥60 GB（本地演练构建时 build cache 可达数 GB）。
6. **拉起服务**（等价于流水线的启动槽位）：
   ```bash
   cd /opt/wwww/corporate_rag
   bash scripts/deploy/deploy.sh            # 8 步：环境判定→依赖→前置检查→ACR 登录→拉镜像→起栈→迁移→健康检查
   ```
   前置检查会校验：`docker-compose.image.yml` / `.env` 非空 / `deploy/nginx/*` / `deploy/postgres/init/`；缺任一报错而非静默降级。

---

## 4. 数据迁移

- **在 app 容器内执行**（`alembic/` 与 `alembic.ini` 已打进镜像），由 `deploy.sh` 的迁移步骤自动完成（含等待 PostgreSQL 就绪 + 幂等重试）。
- 手工重跑（幂等）：
  ```bash
  cd /opt/wwww/corporate_rag
  docker compose -f docker-compose.image.yml exec app alembic upgrade head
  ```
- **顺序**：迁移**先于冒烟**（首版建 8 张表，第二版加 `knowledge_base.domain` 列）。

---

## 5. 冒烟（发布成功判据）

**缺任一步都不得判定发布成功**：

1. 健康检查：`curl -fsS http://127.0.0.1/api/health` → 200。
2. 浏览器打开 `http://<ECS-IP>/` → 用测试账号登录。
3. 建/选一个知识库 → **上传一份文档**。
4. 等该文档状态变为 **`ready`**。
5. 提问 → **回答里带引用来源**。

> 上传步骤专治"nginx 上传上限被挡"，`ready` 专治"迁移未跑/存储凭据不匹配"，引用专治"检索链路断"。这三类正是静态检查覆盖不到、只有真实交付才暴露的。

---

## 6. 日常更新

**正常发布流程见 §2.0**（推代码 `dev-wsl` → Codeup 镜像同步 → 流水线自动打包并生成部署单 → 部署页创建部署单 → 自动主机部署 → 冒烟）。之后日常只需：

- 改 `LLM_MODEL` 等运行配置 → 改服务器上的 `.env` → 重跑部署（或 `deploy.sh start`）；
- 单独体检：`bash scripts/deploy/deploy.sh health_check`；
- 临时停服（维护 / 下线）：`bash scripts/deploy/deploy.sh stop`（**人工运维用**，别接进流水线停止槽位）；
- 回收磁盘：`bash scripts/deploy/deploy.sh clean`（保留最近 2 个 app 镜像 + 清悬空镜像与残留包）。

**发布前置**：制品源于**已提交的 commit** —— 发布前 `git status` 必须为空，未提交改动不得进入发布。

---

## 7. 回滚与备份

**代码 / 版本回滚**：镜像 tag = 构建号，ACR 里所有 tag 都在。

- 首选：重跑一次旧的构建（回到那个构建号对应的代码），产出对应镜像；
- 应急：临时把 `docker-compose.image.yml` 的 `image:` 改指旧的 `deploy_store_local:<旧tag>`，`docker compose -f docker-compose.image.yml up -d app`。

**数据备份**：

- **MinIO 里的原始上传文件是唯一不可再生的源头** —— 丢了不可重建，必须备份；
- PostgreSQL 卷（`postgres_data`）承载元数据与向量，建议与 MinIO 同批备份；
- 频率 / 保留期 / 存放位置：**待定**（见下）。

---

## 待定项（不得当作已决定）

| 项 | 当前口径 | 待定 |
|---|---|---|
| HTTPS | 纯 HTTP（nginx 只有 80） | 是否上 443 + 证书来源 / SLB 终止 TLS |
| Langfuse | 保留在栈内（绑回环，不对外） | 长期去留、是否需对外查看 |
| `docker-compose.prod.yml` | 不参与本目标形态 | 废弃 / 删除 / 与目标形态对齐 |
| 备份口径 | 见 §7（MinIO 必备） | 频率、保留期、是否上 OSS |
| 托管化 | 单机自建（PG / Redis / MinIO 同机） | 是否迁 RDS / Redis / OSS / SLB |
| 首次语料导入 | — | 导入哪批文档、由谁、走界面上传还是灌库 |

> 以上未决项已登记在 `docs/agents/requirements_pool.md`；正式决定前，runbook 只以"当前口径"描述。
