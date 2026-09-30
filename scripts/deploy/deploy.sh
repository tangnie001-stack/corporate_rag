#!/usr/bin/env bash
# 目标机部署（demo 单机）：前置检查 → 拉镜像 → 起栈 → 迁移 → 健康检查
#
# 用法（任意目录均可，脚本会自行切到仓库根）：
#   scripts/deploy/deploy.sh
#
# 前置：目标机已 `docker login <ACR>`（app 镜像在私有库；5 个基镜像在公开库、免登录）
# 幂等：可重复执行；镜像已是最新则不会重复下载，迁移重复执行无副作用
#
# 注意：本脚本假定目标机上有**完整的仓库副本**（clone 或 rsync）——
#       部署档 compose 靠挂载取技能/预设/迁移/nginx 配置等，而非打进镜像。

set -euo pipefail

COMPOSE_FILE="docker-compose.image.yml"
ACR="crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com"
HEALTH_URL="http://127.0.0.1/api/health"   # 经 nginx（80），与对外路径一致
RETRIES=40
INTERVAL=3

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
ROOT=$(pwd)

fail() { echo "[deploy] ✗ $*" >&2; exit 1; }
step() { echo; echo "[deploy] === $* ==="; }
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

step "1/5 前置检查"
echo "[deploy]   仓库根: $ROOT"
command -v docker >/dev/null 2>&1 || fail "未找到 docker"
docker compose version >/dev/null 2>&1 || fail "未找到 docker compose（需 v2 插件）"
command -v curl >/dev/null 2>&1 || fail "未找到 curl（健康检查需要）"
[ -f "$COMPOSE_FILE" ] || fail "缺少 $COMPOSE_FILE —— 需在完整仓库副本上执行"
[ -f .env ] || fail "缺少 .env —— 从 .env.example 复制后填真实值（MinIO 两键、PG/Redis 口令等）"

# 这些路径靠挂载供容器使用；缺任一条会导致**静默降级**（技能/预设清空、应用库不建、迁移不可用），
# 所以必须报错而不是让 Docker 自建空目录
missing=0
for d in deploy/nginx/html deploy/postgres/init skills agents alembic; do
  if [ ! -d "$d" ]; then echo "[deploy]   ✗ 缺目录: $d"; missing=1; fi
done
for f in deploy/nginx/nginx.conf alembic.ini; do
  if [ ! -f "$f" ]; then echo "[deploy]   ✗ 缺文件: $f"; missing=1; fi
done
[ "$missing" = 0 ] || fail "仓库副本不完整 —— 上述路径靠挂载生效，缺失会导致功能静默失效"
mkdir -p data/ragas   # 运行期目录，Docker 也能自建，这里显式建以固定属主

# 仅提示不阻断：公开库的 5 个镜像不需要凭据，pull 失败时再看这里
cfg="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
if ! grep -q "$ACR" "$cfg" 2>/dev/null; then
  echo "[deploy]   ⚠ 未在 $cfg 找到 $ACR 的登录凭据；app 镜像在私有库，pull 会失败"
  echo "[deploy]     请先执行: docker login $ACR"
fi

echo "[deploy]   将要部署的 app 镜像:"
grep -E "deploy_store_local:" "$COMPOSE_FILE" | sed 's/^/     /'

step "2/5 拉取镜像"
compose pull

step "3/5 启动服务"
compose up -d

step "4/5 执行数据库迁移"
# 先等 PostgreSQL 就绪（迁移经 compose 网络连它）；app 不依赖表即可启动，故先迁移无死锁
for i in $(seq 1 20); do
  if compose exec -T postgres pg_isready -U langfuse >/dev/null 2>&1; then
    echo "[deploy]   ✓ PostgreSQL 就绪"
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
  echo "[deploy]   … 第 $i 次失败（app 可能仍在启动），5s 后重试"
  sleep 5
done
[ "$migrated" = 1 ] || fail "数据库迁移失败（app 日志见上；可手动重跑: docker compose -f $COMPOSE_FILE logs app）"

step "5/5 健康检查"
ok=0
for i in $(seq 1 "$RETRIES"); do
  if curl -fsS -o /dev/null "$HEALTH_URL" 2>/dev/null; then
    ok=1
    echo "[deploy]   ✓ 第 $i 次探测通过"
    break
  fi
  echo "[deploy]   … 第 $i/$RETRIES 次未就绪，${INTERVAL}s 后重试"
  sleep "$INTERVAL"
done
if [ "$ok" != 1 ]; then
  compose logs --tail 40 app || true
  fail "应用未在预期时间内就绪（已打印 app 日志尾部）"
fi

echo
echo "[deploy] ✅ 部署完成，健康检查通过: $HEALTH_URL"
echo "[deploy]    手动冒烟：浏览器打开 http://<ECS-IP>/ → 登录 → 上传一份文档 → 等状态 ready → 提问并看引用"
echo "[deploy]    查看日志：docker compose -f $COMPOSE_FILE logs -f app"
