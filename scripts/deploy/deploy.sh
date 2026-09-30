#!/usr/bin/env bash
# 目标机部署（demo 单机）
#   环境判定 → 依赖安装 → 前置检查 → 更新挂载文件 → ACR 登录 → 拉镜像 → 起栈 → 迁移 → 健康检查
#
# 用法：
#   bash scripts/deploy/deploy.sh [--force] [--no-git-pull] [--no-install-deps]
#
#   --force            跳过「开发机 / 环境未知」的拒绝（仅在你明确知道后果时用）
#   --no-git-pull      不做 git pull，沿用当前挂载文件
#   --no-install-deps  缺 git / docker 时不自动安装，直接报错（预装好的机器可用）
#
# 非交互登录 ACR（可选）：
#   ACR_USER=xxx ACR_PASSWORD=yyy bash scripts/deploy/deploy.sh
#
# 标准发布路径：/opt/wwww/corporate_rag（/opt/wwww 为多站点父目录）
#   —— 在别处执行会打印提醒；所有文档 / 备份定时任务都引用该路径
#
# 三点前提：
#   1. 目标机需有**完整仓库副本**（clone 或 rsync）—— 部署档 compose 靠挂载取
#      技能 / 预设 / 迁移 / nginx 配置等，而非打进镜像。（脚本在仓库里，故首次需先 clone）
#   2. 不要在开发机上跑本脚本：部署档与 dev 的 compose project name、容器名完全相同，
#      起栈会顶掉正在跑的 dev 容器（脚本会主动拦截，除非 --force）。
#   3. 缺 git / docker 时脚本会用**国内源**自动安装（需 root 或 sudo）。

set -euo pipefail

COMPOSE_FILE="docker-compose.image.yml"
ACR="crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com"
EXPECTED_ROOT="/opt/wwww/corporate_rag"                               # 标准发布路径
ECS_METADATA="http://100.100.100.200/latest/meta-data/instance-id"    # 阿里云 ECS 元数据服务
METADATA_HOST="100.100.100.200"
HEALTH_URL="http://127.0.0.1/api/health"   # 经 nginx:80，与对外路径一致
RETRIES=40
INTERVAL=3

FORCE=0
SKIP_GIT_PULL=0
INSTALL_DEPS=1

usage() { sed -n '2,25p' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --force)            FORCE=1 ;;
    --no-git-pull)      SKIP_GIT_PULL=1 ;;
    --no-install-deps)  INSTALL_DEPS=0 ;;
    -h|--help)          usage; exit 0 ;;
    *)                  echo "未知参数: $1（-h 看用法）" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
ROOT=$(pwd)

if [ "$(id -u)" = "0" ]; then SUDO=""
elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"
else SUDO=""
fi

info()  { echo "[deploy]   $*"; }
warn()  { echo "[deploy]   ⚠ $*"; }
fail()  { echo "[deploy] ✗ $*" >&2; exit 1; }
step()  { echo; echo "[deploy] === $* ==="; }
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

NGINX_CONF_CHANGED=0
APP_CONTENT_CHANGED=0
DEPLOYED_APP_IMAGE=""

# 阿里云元数据服务可达性（在阿里云 ECS 上必通；开发机 / 自建机不通）
# 优先 curl；无 curl 时用 bash 内建 /dev/tcp 做最小 HTTP 探测
metadata_reachable() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 -o /dev/null "$ECS_METADATA" 2>/dev/null
    return $?
  fi
  exec 9<>"/dev/tcp/${METADATA_HOST}/80" 2>/dev/null || return 1
  printf 'GET /latest/meta-data/instance-id HTTP/1.0\r\nHost: %s\r\n\r\n' "$METADATA_HOST" >&9
  local line=""
  IFS= read -r line <&9 || true
  exec 9<&- 2>/dev/null || true
  case "$line" in *" 200 "*) return 0 ;; *) return 1 ;; esac
}

step "1/9 环境判定"
# 判据优先级：① DEPLOY_ROLE 显式值 > ② WSL（必为开发机，零依赖）> ③ 阿里云元数据服务（必为云上发布机）
ROLE="${DEPLOY_ROLE:-}"
if [ -z "$ROLE" ]; then
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
    ROLE="dev"
  elif metadata_reachable; then
    ROLE="prod"
  else
    ROLE="unknown"
  fi
fi

case "$ROLE" in
  prod)
    iid=""
    if command -v curl >/dev/null 2>&1; then
      iid=$(curl -fsS --max-time 2 "$ECS_METADATA" 2>/dev/null || echo "?")
    fi
    info "环境: 发布机（阿里云 ECS${iid:+ instance-id=$iid}）"
    ;;
  dev)
    info "环境: 开发机（WSL）"
    [ "$FORCE" = 1 ] || fail "本脚本用于目标机部署，开发机上运行会顶掉 dev 容器。确要执行请加 --force"
    warn "--force 已指定，继续（后果自负）"
    ;;
  *)
    info "环境: 未识别（非 WSL，且阿里云元数据服务不可达）"
    [ "$FORCE" = 1 ] || fail "无法确认这是发布机。若是自建 / 非阿里云主机，请加 --force 或设 DEPLOY_ROLE=prod"
    warn "--force 已指定，继续"
    ;;
esac

step "2/9 依赖安装（git / docker，走国内源）"
if [ "$INSTALL_DEPS" != 1 ]; then
  info "跳过（--no-install-deps）"
else
  PKG=""
  if command -v apt-get >/dev/null 2>&1; then PKG="apt"
  elif command -v dnf >/dev/null 2>&1; then PKG="dnf"
  elif command -v yum >/dev/null 2>&1; then PKG="yum"
  fi
  [ -n "$PKG" ] || fail "无法识别包管理器（需 apt / dnf / yum）；请手动安装 git 与 docker compose"
  if [ -z "$SUDO" ] && [ "$(id -u)" != "0" ]; then
    fail "安装依赖需要 root 权限：请用 root 执行，或先装好 sudo"
  fi
  info "包管理器: $PKG${SUDO:+（经 sudo）}"

  # ---- git ----
  if command -v git >/dev/null 2>&1; then
    info "git 已安装: $(git --version)"
  else
    info "未找到 git → 安装"
    case "$PKG" in
      apt)     $SUDO apt-get update -y && $SUDO apt-get install -y git ;;
      dnf|yum) $SUDO "$PKG" install -y git ;;
    esac
    command -v git >/dev/null 2>&1 || fail "git 安装失败"
    info "git 安装完成: $(git --version)"
  fi

  # ---- docker + compose 插件 ----
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    info "docker 已安装: $(docker --version)"
  else
    info "未找到 docker / compose 插件 → 用阿里云镜像源安装"
    case "$PKG" in
      apt)
        $SUDO apt-get update -y
        $SUDO apt-get install -y ca-certificates curl gnupg
        $SUDO install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg \
          | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
        codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")
        [ -n "$codename" ] || fail "无法确定发行版代号（/etc/os-release 缺 UBUNTU_CODENAME / VERSION_CODENAME）"
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $codename stable" \
          | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
        $SUDO apt-get update -y
        $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
      dnf|yum)
        # 直接写 repo 文件，避免依赖 yum-utils / dnf-plugins-core
        $SUDO tee /etc/yum.repos.d/docker-ce.repo >/dev/null <<'REPO'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://mirrors.aliyun.com/docker-ce/linux/centos/$releasever/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://mirrors.aliyun.com/docker-ce/linux/centos/gpg
REPO
        # Alibaba Cloud Linux 3 等发行版的 $releasever 不被 docker-ce 仓支持 → 按 RHEL 兼容大版本固定
        rel=$(rpm -E '%{rhel}' 2>/dev/null || true)
        case "$rel" in ''|*[!0-9]*) rel=8 ;; esac
        [ "$rel" -ge 7 ] 2>/dev/null || rel=8
        $SUDO sed -i "s/\$releasever/$rel/" /etc/yum.repos.d/docker-ce.repo
        info "docker-ce 源: 阿里云镜像（releasever=$rel）"
        $SUDO "$PKG" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
    esac
    $SUDO systemctl enable --now docker >/dev/null 2>&1 \
      || $SUDO service docker start >/dev/null 2>&1 \
      || warn "docker 服务未能自动启动，请手动检查（systemctl status docker）"
    command -v docker >/dev/null 2>&1 || fail "docker 安装失败"
    docker compose version >/dev/null 2>&1 || fail "docker compose 插件未安装成功"
    info "docker 安装完成: $(docker --version)"
  fi
fi

step "3/9 前置检查"
info "仓库根: $ROOT"
if [ "$ROOT" != "$EXPECTED_ROOT" ]; then
  warn "标准发布路径是 $EXPECTED_ROOT，当前在 $ROOT"
  warn "文档 / 备份定时任务都按标准路径写；非标准路径请自行保持一致"
fi
[ -f "$COMPOSE_FILE" ] || fail "缺少 $COMPOSE_FILE —— 需在完整仓库副本上执行"
[ -f .env ] || fail "缺少 .env —— 从 .env.example 复制后填真实值（MinIO 两键、PG/Redis 口令等）"

# 这些路径靠挂载供容器使用；缺任一条会导致**静默降级**（技能/预设清空、应用库不建、迁移不可用），
# 所以必须报错，而不是让 Docker 自建空目录
missing=0
for d in deploy/nginx/html deploy/postgres/init skills agents alembic; do
  if [ ! -d "$d" ]; then info "✗ 缺目录: $d"; missing=1; fi
done
for f in deploy/nginx/nginx.conf alembic.ini; do
  if [ ! -f "$f" ]; then info "✗ 缺文件: $f"; missing=1; fi
done
[ "$missing" = 0 ] || fail "仓库副本不完整 —— 上述路径靠挂载生效，缺失会导致功能静默失效"
mkdir -p data/ragas   # 运行期目录，Docker 也能自建，这里显式建以固定属主

step "4/9 更新挂载文件（git pull）"
if [ "$SKIP_GIT_PULL" = 1 ]; then
  info "跳过（--no-git-pull）"
elif ! command -v git >/dev/null 2>&1; then
  warn "未找到 git —— 跳过更新，沿用当前挂载文件"
elif [ ! -d .git ]; then
  warn "当前目录不是 git 仓库 —— 跳过更新，沿用当前挂载文件"
else
  before=$(git rev-parse HEAD)
  info "当前: $(git rev-parse --short HEAD) @ $(git rev-parse --abbrev-ref HEAD)"
  if git pull --ff-only; then
    after=$(git rev-parse HEAD)
    if [ "$before" = "$after" ]; then
      info "✓ 已是最新"
    else
      info "✓ 已更新 $(git rev-parse --short "$before") → $(git rev-parse --short "$after")，涉及文件："
      changed=$(git diff --name-only "$before" "$after")
      echo "$changed" | sed 's/^/     /'
      echo "$changed" | grep -qx 'deploy/nginx/nginx.conf' && NGINX_CONF_CHANGED=1
      echo "$changed" | grep -qE '^(skills|agents)/'     && APP_CONTENT_CHANGED=1
      info "注：src/ 的变更**不会**影响运行中的容器（代码在镜像里，非挂载）"
    fi
  else
    warn "git pull 失败（无凭据 / 有本地改动 / 分支分歧）—— 沿用当前文件继续"
    warn "要强制以远端为准：git fetch && git reset --hard @{u}（会丢弃本地改动）"
  fi
fi

step "5/9 校验 ACR 登录"
DEPLOYED_APP_IMAGE=$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(.*deploy_store_local:[^[:space:]]*\).*/\1/p' "$COMPOSE_FILE" | head -1)
[ -n "$DEPLOYED_APP_IMAGE" ] || fail "无法从 $COMPOSE_FILE 解析出 app 镜像引用"
info "app 镜像: $DEPLOYED_APP_IMAGE"

if docker manifest inspect "$DEPLOYED_APP_IMAGE" >/dev/null 2>&1; then
  info "✓ 已登录（app 镜像在私有库，5 个基镜像在公开库）"
else
  info "未登录或镜像不可达，开始登录 $ACR"
  if [ -n "${ACR_USER:-}" ] && [ -n "${ACR_PASSWORD:-}" ]; then
    printf '%s' "$ACR_PASSWORD" | docker login -u "$ACR_USER" --password-stdin "$ACR" \
      || fail "docker login 失败"
  else
    [ -t 0 ] || fail "非交互环境无法输入密码 —— 请用 ACR_USER / ACR_PASSWORD 环境变量提供凭据"
    docker login "$ACR" || fail "docker login 失败"
  fi
  docker manifest inspect "$DEPLOYED_APP_IMAGE" >/dev/null 2>&1 \
    || fail "登录后仍取不到该镜像 —— 多半是 tag 不存在（云效每次构建的 tag 会变，需同步 $COMPOSE_FILE 里的 tag）"
  info "✓ 登录成功"
fi

step "6/9 拉取镜像"
compose pull

step "7/9 启动服务"
compose up -d

if [ "$NGINX_CONF_CHANGED" = 1 ]; then
  info "nginx.conf 有更新 → 重载 nginx（up -d 不会重建未变更的服务）"
  compose exec -T nginx nginx -s reload || warn "重载失败，旧配置继续生效（检查 nginx.conf 语法）"
fi
if [ "$APP_CONTENT_CHANGED" = 1 ]; then
  info "skills/ 或 agents/ 有更新 → 重启 app 以重新加载"
  compose restart app >/dev/null
fi

step "8/9 执行数据库迁移"
# 先等 PostgreSQL 就绪（迁移经 compose 网络连它）；app 不依赖表即可启动，故先迁移无死锁
for i in $(seq 1 20); do
  if compose exec -T postgres pg_isready -U langfuse >/dev/null 2>&1; then
    info "✓ PostgreSQL 就绪"
    break
  fi
  if [ "$i" = 20 ]; then fail "PostgreSQL 未在预期时间内就绪"; fi
  sleep 2
done

# app 可能仍在启动中，exec 会瞬时失败；迁移幂等，故重试
migrated=0
for i in $(seq 1 10); do
  if compose exec -T app alembic upgrade head; then
    migrated=1
    break
  fi
  info "… 第 $i 次失败（app 可能仍在启动），5s 后重试"
  sleep 5
done
[ "$migrated" = 1 ] || fail "数据库迁移失败（可手动排查: docker compose -f $COMPOSE_FILE logs app）"

step "9/9 健康检查"
ok=0
command -v curl >/dev/null 2>&1 || fail "未找到 curl（健康检查需要）"
for i in $(seq 1 "$RETRIES"); do
  if curl -fsS -o /dev/null "$HEALTH_URL" 2>/dev/null; then
    ok=1
    info "✓ 第 $i 次探测通过"
    break
  fi
  info "… 第 $i/$RETRIES 次未就绪，${INTERVAL}s 后重试"
  sleep "$INTERVAL"
done
if [ "$ok" != 1 ]; then
  compose logs --tail 40 app || true
  fail "应用未在预期时间内就绪（已打印 app 日志尾部）"
fi

echo
echo "[deploy] ✅ 部署完成，健康检查通过: $HEALTH_URL"
echo "[deploy]    发布路径: $ROOT"
echo "[deploy]    镜像: $DEPLOYED_APP_IMAGE"
echo "[deploy]    手动冒烟：浏览器打开 http://<ECS-IP>/ → 登录 → 上传一份文档 → 等状态 ready → 提问并看引用"
echo "[deploy]    查看日志：docker compose -f $COMPOSE_FILE logs -f app"
