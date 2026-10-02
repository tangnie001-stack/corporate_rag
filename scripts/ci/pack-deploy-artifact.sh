#!/usr/bin/env bash
# 构建期打包：产出「目标机部署制品」（云效流水线构建阶段的执行命令就是跑本脚本）
#
# 用法：
#   bash scripts/ci/pack-deploy-artifact.sh [--tag <app镜像tag>] [--out <目录>]
#
#   --tag <tag>  显式指定 app 镜像 tag（本地演练用）；默认取流水线内置变量 $BUILD_NUMBER
#   --out <dir>  暂存目录，相对仓库根，默认 pack-staging
#
# 为什么放仓库里而不是内联在流水线配置里：
#   ① 云效「执行命令」有字符数限制，官方建議长脚本放回代码库；
#   ② 本脚本的白名单与 scripts/deploy/deploy.sh 第 3 步的前置检查是**同一份契约**，
#      必须随代码一起演进、一起评审，内联在流水线里必然漂移。
#
# 制品内容（白名单，3 项）：
#   docker-compose.image.yml   目标机部署档（app 镜像 tag 由本脚本注入本次构建号）
#   deploy/                    宿主挂载来源：nginx 配置与页面、postgres 初始化脚本
#   scripts/deploy/deploy.sh   部署脚本本身
#
#   不含 src/ scripts/(其余) skills/ agents/ alembic/ alembic.ini —— 它们已打进 app 镜像；
#   不含 .env* —— 密钥不进制品，且 .env.example 在目标机无任何用途（只会招来
#   「cp .env.example .env」这条通往「半空 .env + compose 兜底弱口令」的捷径）。
#
# 制品布局刻意与仓库根一致：deploy.sh 会 cd 到自身路径的上两级，
#   解压到 /opt/wwww/corporate_rag 后正好指回根目录。
#
# 为什么 compose 文件**不改名**成 docker-compose.yml：
#   目标机目录可能同时存在 docker-compose.override.yml（调试期挂 ./src ./skills ./agents）。
#   compose 只在默认文件名下才自动加载 override；显式文件名（本文件）不会。
#   改名会让 override 静默生效，用宿主代码遮住镜像里烘好的代码。

set -euo pipefail

OUT_DIR="pack-staging"
TAG=""

# 打印文件头的用法块（首行 shebang 之后的连续 # 注释行）
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --tag)     TAG="${2:?--tag 需要参数}"; shift 2 ;;
    --out)     OUT_DIR="${2:?--out 需要参数}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)         echo "未知参数: $1（-h 看用法）" >&2; exit 2 ;;
  esac
done

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT=$(pwd)
info() { echo "[pack] $*"; }
fail() { echo "[pack] ✗ $*" >&2; exit 1; }

# ---- 1. 定 tag：宁可失败，也不要打出 tag 为空的 compose ----
if [ -z "$TAG" ]; then
  if [ -n "${BUILD_NUMBER:-}" ]; then
    TAG="$BUILD_NUMBER"
  else
    fail "未取到 app 镜像 tag —— 请设置 BUILD_NUMBER（流水线内置变量），或显式传 --tag"
  fi
fi
info "仓库根: $REPO_ROOT"
info "app 镜像 tag: $TAG"

# ---- 2. 校验：白名单里的每一项都必须存在 ----
missing=0
for f in docker-compose.image.yml \
         deploy/nginx/nginx.conf \
         scripts/deploy/deploy.sh; do
  if [ -f "$f" ]; then info "  OK   $f"; else info "  MISS $f"; missing=1; fi
done
for d in deploy/nginx/html deploy/postgres/init; do
  if [ -d "$d" ]; then
    info "  OK   $d/ ($(find "$d" -type f | wc -l) 个文件)"
  else
    info "  MISS $d/"; missing=1
  fi
done
[ "$missing" = 0 ] || fail "仓库不完整，中止打包"

# ---- 3. 暂存（白名单拷贝；cp -p 保留 deploy.sh 的可执行位）----
STAGE="$REPO_ROOT/$OUT_DIR"
rm -rf "$STAGE"
mkdir -p "$STAGE/scripts"
cp -p  docker-compose.image.yml "$STAGE/"
cp -rp deploy                   "$STAGE/"
cp -rp scripts/deploy           "$STAGE/scripts/"

# ---- 4. 注入本次构建号，消除「人工同步 compose 里的 tag」 ----
sed -i "s|deploy_store_local:[A-Za-z0-9_.-]*|deploy_store_local:${TAG}|" \
  "$STAGE/docker-compose.image.yml"
info "  → compose 内 app 镜像: $(grep -oE 'deploy_store_local:[A-Za-z0-9_.-]+' "$STAGE/docker-compose.image.yml" | head -1)"

# ---- 5. 兜底：制品里不得出现任何 .env* ----
if find "$STAGE" -name '.env*' | grep -q .; then
  fail "staging 内出现 .env* —— 密钥不得进制品"
fi

# ---- 6. 打包前确认制品内容 ----
echo
info "===== 制品内容 ====="
(cd "$STAGE" && find . -type f | sort)
info "合计 $(du -sh "$STAGE" | cut -f1) —— 交由流水线的 ArtifactUpload 步骤上传（path 填 $OUT_DIR/）"
