# ADR-0019：Langfuse 放开公网访问（`:3000` 对外）

- **Status**：Accepted
- **Date**：2026-10-09
- **Deciders**：用户（决策，明确选择"先跑起来、不加网络层认证"）；助手（诊断、执行与记录）
- **Supersedes**：ADR-0011 的「暴露面收敛到仅回环」（局部）

## 背景与问题

目标机 `8.133.218.201`（阿里云 ECS，demo 单机）需要**远程用浏览器查看 Langfuse UI**（查 project/API Key、看 trace）。

现状与实测（2026-10-09）：

- `langfuse-web` 容器**在跑**：`docker ps` → `corporate-rag-langfuse-web | Up 4 hours | 127.0.0.1:3000->3000/tcp`
- 但端口**只绑回环**：`ss -ltnp` → `LISTEN 127.0.0.1:3000 ... docker-proxy`
- 安全组原先**只放行 22 与 80**（runbook §1.3）
- ⇒ 公网 `http://8.133.218.201:3000` 从「超时」变为「`Connection refused`」（安全组放开后，包到了主机但主机在公网地址上无监听）

触发事件：用户接到需求要远程看 Langfuse，已自行放开安全组 3000，仍打不开，定位到**端口绑定**这一层。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 保持回环 + SSH 隧道 | `ssh -L 3000:127.0.0.1:3000 root@<ECS-IP>` | 每台客户端都要建隧道，不便团队 | 不扩大攻击面；不动 ADR-0011 |
| **B 改端口对外（选）** | compose 端口 `3000:3000` + 安全组放行 3000 + 目标机 `.env` 设 `NEXTAUTH_URL` | EOL 服务直接挂公网、无网络层防护 | 浏览器直连，零客户端配置 |
| C nginx 反代（可加证书） | 在 `deploy/nginx/nginx.conf` 加 server block，走 compose 网络直连 `langfuse-web:3000` | 需域名/证书；仍是公网暴露 | 复用 80/443，便于加 HTTPS |
| D 加认证后对外 | B 或 C + （`AUTH_DISABLE_SIGNUP` 或 nginx basic auth）| 多一层配置 | 显著降低暴露风险 |

## 决策

**选 B，并叠加 `AUTH_DISABLE_SIGNUP=true` 关闭自助注册。** 把 `docker-compose.image.yml` 的 `langfuse-web` 端口由 `"127.0.0.1:3000:3000"` 改为 `"3000:3000"`，并**在部署档里硬编码** `AUTH_DISABLE_SIGNUP: "true"`（与 app 的 `WECOM_BOT_ENABLED: "true"` 同款做法）；安全组放行 3000；目标机 `.env` 设 `NEXTAUTH_URL=http://8.133.218.201:3000`。**不引入 IP 白名单 / `auth_basic` 这类网络层认证**（用户裁定）。

## 理由

关键一行：**demo 阶段以"先能看到"为先，Langfuse 承载的数据为空/低敏感，且它不在请求路径上** —— ADR-0011 已实测「langfuse-web 停掉后应用仍能完整跑完一轮问答」，所以把暴露风险加在一个旁路服务上，影响面可控。

用户判断：先跑起来；认证/HTTPS 等属"后面再优化"。

**为什么叠加"关闭注册"而不是加网络层认证**：`AUTH_DISABLE_SIGNUP` 是本仓镜像（`langfuse/langfuse:2.95.11`）**服务端运行时读取**的变量（实测镜像内 `/app/web/.next/server/chunks/5463.js`：`AUTH_DISABLE_SIGNUP: process.env.AUTH_DISABLE_SIGNUP`），效果等于"只有已播种的账号能登"，**一次配置、零运维**；而 `auth_basic` / IP 白名单要么需额外组件、要么锁死来源，与"先跑起来"冲突。注意 `NEXT_PUBLIC_SIGN_UP_DISABLED` 带 `NEXT_PUBLIC_` 前缀、构建期内联，**改了大概率不生效**，不能用它。

## 后果

**正面**：

- 公网浏览器直连 `http://8.133.218.201:3000`，无需隧道或客户端配置；团队任何人都能看。

**负面 / 接受的代价**：

- **ADR-0011 为 `2.95.11`（EOL、无安全补丁承诺）设置的结构性对冲「暴露面收敛到仅回环」被移除**。缓解降级为两条：① 关闭自助注册（`AUTH_DISABLE_SIGNUP=true`，陌生人无法自行开号）；② Langfuse 自身的账号登录。**仍无网络层防护**（无 IP 白名单 / 无 `auth_basic` / 无限流）；且若 `.env` 的 `NEXTAUTH_SECRET` / `LANGFUSE_SALT` 仍是占位符，这两条防线的强度都要打问号。
- 依赖 `.env` 的 `NEXTAUTH_URL` 与**实际访问地址严格一致**，否则登录后重定向失效（这是本方案最容易踩的坑）。
- `AUTH_DISABLE_SIGNUP` **必须走 compose 的 `environment:`**（该服务**没有** `env_file: .env`）。本决策选择**硬编码**该值，而非 `${VAR}` + `.env` —— 后者会让这个键进入 `deploy.sh` 第 3 步的 `.env` 必填清单，**主机 `.env` 缺键即部署失败**（2026-10-09 实测踩中：制品已是新版，却因缺键在部署第 3 步中止，端口改动始终没生效）。

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

- **不加 HTTPS** —— 纯 HTTP，登录凭据明文传输。
- **不限制来源** —— 无 IP 白名单、无 `auth_basic`、无限流；仅关闭了自助注册（已播种账号仍可登）。
- **不管密钥强度** —— `NEXTAUTH_SECRET` / `LANGFUSE_SALT` / `LANGFUSE_ENCRYPTION_KEY` 是否已从占位符换成强随机值，属**独立安全项**（其中 `ENCRYPTION_KEY` 有数据后不可再改）。
- **不改用 nginx 反代** —— 仍是裸 `:3000`，不是 80/443 转发。

## 复查触发条件

- **再启用任何网络层缓解**（nginx `auth_basic`、IP 白名单、限流）**或 HTTPS** —— 需更新本 ADR 的「接受的代价」段；
- **需要新增 Langfuse 账号 / 放开注册** —— `AUTH_DISABLE_SIGNUP=true` 会连"被邀请但尚无账号"的人也挡住，届时按需放开或改用 SSO；
- 收到异常访问，或 Langfuse 开始承载真实业务数据；
- Langfuse 升级 / 替换（与 ADR-0011 的复查条件联动）。
