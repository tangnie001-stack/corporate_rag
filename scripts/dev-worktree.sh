#!/usr/bin/env bash
# scripts/dev-worktree.sh — 在任意 worktree 里本地起「后端 uvicorn + 前端反代」。
#
# 解决问题：本仓只有一套容器（工程名/容器名/端口全写死），worktree 无法各跑一套。
# 于是 worktree 里改为「本地跑」：后端用宿主 .venv 起 uvicorn，前端用一个
# 一次性 nginx 容器反代静态文件与 /api。两者都按 slot 分配端口，worktree 之间不冲突。
#
# 端口约定：slot N → 后端 8000+N、前端 8079+N（slot 1 = 8001/8080）。
# 两个 worktree 只要给不同 slot 即可并存。端口记在 <worktree>/.dev-ports（已 gitignore）。
#
# 用法：scripts/dev-worktree.sh <up|down|status> [--slot N] [--backend-port P] [--frontend-port Q] [--force]
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
BASE="$(basename "$ROOT")"
STATE="$ROOT/.dev-ports"
PIDFILE="$ROOT/.dev-uvicorn.pid"
LOGDIR="$ROOT/logs"
LOG="$LOGDIR/dev-uvicorn.log"
CONTAINER="rag-dev-nginx-$BASE"
IMAGE="${DEV_NGINX_IMAGE:-corporate_rag-nginx:latest}"
TEMPLATE="$ROOT/deploy/nginx/nginx.dev.conf.template"

usage() {
    cat <<'EOF'
用法：scripts/dev-worktree.sh <up|down|status> [选项]

选项：
  --slot N            端口槽位（默认 1）：后端 8000+N，前端 8079+N
  --backend-port P    显式指定后端端口（覆盖 slot）
  --frontend-port Q   显式指定前端端口（覆盖 slot）
  --force             端口被占用时仍强行启动（默认直接报错退出）
  -h, --help          显示本帮助

产物（均已 gitignore）：
  .dev-ports          本次使用的端口；down 时删除
  .dev-uvicorn.pid    本地 uvicorn 的 pid 文件
  logs/dev-uvicorn.log 本地后端日志

镜像：默认 corporate_rag-nginx:latest，可用 DEV_NGINX_IMAGE 覆盖。
EOF
}

port_in_use() {
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1$"
}

# 按「谁在监听该端口」结束进程：--reload 的 uvicorn 有 supervisor+子进程，
# 且 /mnt/d 上进程可能停在 D 状态（SIGTERM 打不进），单靠 pidfile 杀不干净。
kill_port() {
    local port="$1" i=0 pids
    while [ "$i" -lt 15 ]; do
        pids=$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
        [ -z "$pids" ] && return 0
        for p in $pids; do kill "$p" 2>/dev/null || true; done
        sleep 1
        i=$((i + 1))
    done
    pids=$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
    for p in $pids; do kill -9 "$p" 2>/dev/null || true; done
}

cmd="${1:-}"
shift || true

slot=1
backend_port=""
frontend_port=""
force=0

while [ $# -gt 0 ]; do
    case "$1" in
        --slot) slot="${2:?--slot 需要数值}"; shift 2 ;;
        --backend-port) backend_port="${2:?}"; shift 2 ;;
        --frontend-port) frontend_port="${2:?}"; shift 2 ;;
        --force) force=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数：$1" >&2; usage; exit 2 ;;
    esac
done

case "$cmd" in
    up)
        if [ ! -f "$TEMPLATE" ]; then
            echo "缺少模板 $TEMPLATE —— 该分支可能落后于 dev-wsl，请先合并/切换。" >&2
            exit 1
        fi
        if [ -z "$backend_port" ] || [ -z "$frontend_port" ]; then
            if [ -f "$STATE" ] && [ -z "${backend_port}${frontend_port}" ]; then
                read -r s_backend s_frontend < "$STATE"
                backend_port="$s_backend"; frontend_port="$s_frontend"
                echo "复用 .dev-ports：后端 $backend_port / 前端 $frontend_port"
            else
                [ -n "$backend_port" ] || backend_port=$((8000 + slot))
                [ -n "$frontend_port" ] || frontend_port=$((8079 + slot))
            fi
        fi

        if [ "$force" -eq 0 ]; then
            for p in "$backend_port" "$frontend_port"; do
                if port_in_use "$p"; then
                    echo "端口 $p 已被占用；换 --slot，或加 --force。" >&2
                    exit 1
                fi
            done
        fi

        mkdir -p "$LOGDIR"
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "后端已在运行：pid $(cat "$PIDFILE")（如需重启先 down）"
        else
            ( cd "$ROOT"; POSTGRES_HOST=localhost nohup .venv/bin/python -m uvicorn \
                src.main:app --host 0.0.0.0 --port "$backend_port" --reload \
                >"$LOG" 2>&1 & echo $! >"$PIDFILE" )
            echo "后端启动中：pid $(cat "$PIDFILE")，日志 $LOG"
        fi

        printf '%s %s\n' "$backend_port" "$frontend_port" > "$STATE"

        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        docker run -d --name "$CONTAINER" -p "${frontend_port}:80" \
            --add-host=host.docker.internal:host-gateway \
            -e NGINX_ENVSUBST_FILTER='^BACKEND_PORT$' \
            -e "BACKEND_PORT=${backend_port}" \
            -v "$TEMPLATE:/etc/nginx/templates/default.conf.template:ro" \
            -v "$ROOT/deploy/nginx/html:/usr/share/nginx/html:ro" \
            "$IMAGE" >/dev/null
        echo "前端反代容器已启动：$CONTAINER"

        # 首次冷启动在 /mnt/d（9p）上可能耗时约 1 分钟，故等待放宽到 180s
        healthy=0
        for _ in $(seq 1 180); do
            if curl -sf -o /dev/null "http://127.0.0.1:${backend_port}/api/health"; then
                healthy=1
                break
            fi
            sleep 1
        done

        echo
        if [ "$healthy" -eq 1 ]; then
            echo "后端已就绪。"
        else
            echo "⚠ 后端 180s 内未就绪；反代此时会返回 502。看日志：$LOG"
        fi
        echo "前端  http://localhost:${frontend_port}"
        echo "接口  http://localhost:${backend_port}/docs"
        echo "停止  scripts/dev-worktree.sh down"
        ;;
    down)
        backend_port=""
        if [ -f "$STATE" ]; then
            read -r backend_port _ < "$STATE"
        fi
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        if [ -f "$PIDFILE" ]; then
            pid="$(cat "$PIDFILE")"
            pkill -P "$pid" >/dev/null 2>&1 || true
            kill "$pid" >/dev/null 2>&1 || true
            rm -f "$PIDFILE"
        fi
        # 兜底：按端口清掉残留（--reload 子进程 / D 状态导致的杀不干净）
        if [ -n "$backend_port" ]; then
            kill_port "$backend_port"
        fi
        rm -f "$STATE"
        echo "已停止（容器 $CONTAINER + 本地后端）"
        ;;
    status)
        if [ -f "$STATE" ]; then
            read -r s_backend s_frontend < "$STATE"
            echo "端口：后端 $s_backend / 前端 $s_frontend"
        else
            echo "端口：未启动（无 .dev-ports）"
        fi
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "后端：运行中 pid $(cat "$PIDFILE")"
        else
            echo "后端：未运行"
        fi
        if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
            echo "前端容器：运行中（$CONTAINER）"
        else
            echo "前端容器：未运行"
        fi
        ;;
    ""|-h|--help)
        usage
        ;;
    *)
        echo "未知子命令：$cmd" >&2
        usage
        exit 2
        ;;
esac
