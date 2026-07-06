#!/bin/bash
# wait-for-it.sh — wait for a TCP host:port to be available
# Usage: ./wait-for-it.sh host:port [-t timeout] [-- command args]

HOST=$(echo "$1" | cut -d: -f1)
PORT=$(echo "$1" | cut -d: -f2)
shift
TIMEOUT=30

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -t) TIMEOUT="$2"; shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done

echo "Waiting for $HOST:$PORT (timeout: ${TIMEOUT}s)..."
for i in $(seq 1 "$TIMEOUT"); do
    nc -z "$HOST" "$PORT" 2>/dev/null && break
    sleep 1
done

if nc -z "$HOST" "$PORT" 2>/dev/null; then
    echo "$HOST:$PORT is available"
    exec "$@"
else
    echo "Timeout waiting for $HOST:$PORT"
    exit 1
fi
