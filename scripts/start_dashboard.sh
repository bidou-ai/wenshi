#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
用法: ./scripts/start_dashboard.sh

在 Ubuntu 本机启动 Wenshi 浏览器后台，按 Ctrl+C 停止。
可选环境变量:
  WENSHI_DASHBOARD_PORT  监听端口，默认 8088
  WENSHI_RUN_ROOT       巡检目录，默认 runtime/runs
  WENSHI_ADMIN_PIN      管理员 PIN；留空时删除和去重重置功能禁用
EOF
  exit 0
fi

HOST="127.0.0.1"
PORT="${WENSHI_DASHBOARD_PORT:-8088}"
RUN_ROOT="${WENSHI_RUN_ROOT:-$ROOT/runtime/runs}"
ADMIN_PIN="${WENSHI_ADMIN_PIN:-}"
URL="http://${HOST}:${PORT}/"
LOG_PATH="$ROOT/runtime/dashboard.log"

export no_proxy="${no_proxy:+$no_proxy,}127.0.0.1,localhost"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"

mkdir -p "$RUN_ROOT" "$(dirname "$LOG_PATH")"
cd "$ROOT"
printf '\n[%s] start host=%s port=%s root=%s\n' "$(date --iso-8601=seconds)" "$HOST" "$PORT" "$RUN_ROOT" >>"$LOG_PATH"

python3 -u "$ROOT/dashboard/server.py" \
  --root "$RUN_ROOT" \
  --host "$HOST" \
  --port "$PORT" \
  --pin "$ADMIN_PIN" \
  >>"$LOG_PATH" 2>&1 &
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
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://{host}:{port}/", timeout=0.3) as response:
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
if [[ -z "$ADMIN_PIN" ]]; then
  echo "管理员 PIN 未配置：删除和去重重置功能已禁用。"
fi
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || echo "请手动打开: $URL"
else
  echo "未找到 xdg-open，请手动打开: $URL"
fi

wait "$SERVER_PID"
