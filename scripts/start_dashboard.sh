#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="127.0.0.1"
PORT="${WENSHI_DASHBOARD_PORT:-8088}"
RUN_ROOT="${WENSHI_RUN_ROOT:-$ROOT/runtime/runs}"
URL="http://${HOST}:${PORT}/"
LOG_PATH="$ROOT/runtime/dashboard.log"

mkdir -p "$RUN_ROOT" "$(dirname "$LOG_PATH")"
cd "$ROOT"

python3 -u "$ROOT/dashboard/server.py" \
  --root "$RUN_ROOT" \
  --host "$HOST" \
  --port "$PORT" \
  >"$LOG_PATH" 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
for _ in $(seq 1 30); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "后台启动失败，日志: $LOG_PATH" >&2
    cat "$LOG_PATH" >&2 || true
    exit 1
  fi
  if python3 - "$HOST" "$PORT" <<'PY'
import sys
import urllib.request

host, port = sys.argv[1:]
try:
    with urllib.request.urlopen(f"http://{host}:{port}/", timeout=0.3) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    ready=1
    break
  fi
  sleep 0.1
done

if [[ "$ready" != 1 ]]; then
  echo "后台未能在预期时间内启动，日志: $LOG_PATH" >&2
  exit 1
fi

echo "Wenshi 后台已启动: $URL"
echo "浏览器会在 Ubuntu 本机打开；按 Ctrl+C 停止后台。"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || echo "请手动打开: $URL"
else
  echo "未找到 xdg-open，请手动打开: $URL"
fi

wait "$SERVER_PID"
