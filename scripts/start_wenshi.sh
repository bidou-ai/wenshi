#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}"

usage() {
  cat <<'EOF'
用法: ./scripts/start_wenshi.sh [phenotype] [--check]

默认依次执行离线环境检查、真实硬件连通检查，再启动相机桥、RViz 和巡检控制台。
  --check  只执行环境与硬件检查，不启动任何运行组件

可用 WENSHI_CONFIG 指定配置文件。正式启动后在控制台输入 start 或 start loop。
phenotype 用于 32 株、16 个停车点的正式表型任务；未标定时会在创建运动组件前退出。
EOF
}

MODE="rice"
if [[ "${1:-}" == "phenotype" ]]; then
  MODE="phenotype"
  shift
fi

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --check|"")
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
export no_proxy="${no_proxy:+$no_proxy,}192.168.192.5,192.168.192.160,192.168.192.203"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}192.168.192.5,192.168.192.160,192.168.192.203"

"$ROOT/scripts/check_environment.sh"

if [[ "$MODE" == "phenotype" ]]; then
  python3 - "$CONFIG" "$ROOT/runtime/runs" <<'PY'
import sys
from pathlib import Path
from wenshi_patrol.config import load_config
from wenshi_patrol.phenotyping.preflight import phenotyping_preflight

config = load_config(Path(sys.argv[1]))
report = phenotyping_preflight(config, Path(sys.argv[2]))
if not report.ok or not report.formal_ready:
    print("表型任务配置未完成，禁止启动运动组件。", file=sys.stderr)
    for error in report.errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
PY
fi

"$ROOT/scripts/check_hardware_links.sh"

if [[ "${1:-}" == "--check" ]]; then
  echo "[OK] 环境与硬件检查通过；未启动巡检组件"
  exit 0
fi

STAMP="$(date +%Y%m%d_%H%M%S_%N)"
export WENSHI_RUN_DIR="$ROOT/runtime/runs/run_$STAMP"
mkdir -p "$WENSHI_RUN_DIR/ros"
export ROS_LOG_DIR="$WENSHI_RUN_DIR/ros"

PIDS=()
cleanup() {
  set +e
  for pid in "${PIDS[@]:-}"; do
    kill -INT "$pid" 2>/dev/null || true
  done
  wait "${PIDS[@]:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "运行目录: $WENSHI_RUN_DIR"
echo "主事件日志: $WENSHI_RUN_DIR/system.log"
echo "控制台日志: $WENSHI_RUN_DIR/console.log"
echo "启动 D435 ROS2 桥..."
python3 -m wenshi_patrol.camera_bridge --config "$CONFIG" \
  >"$WENSHI_RUN_DIR/camera_console.log" 2>&1 &
PIDS+=("$!")

if command -v rviz2 >/dev/null 2>&1; then
  echo "启动 RViz..."
  rviz2 -d "$ROOT/config/wenshi.rviz" >"$WENSHI_RUN_DIR/rviz.log" 2>&1 &
  PIDS+=("$!")
fi

if [[ "$MODE" == "phenotype" ]]; then
  echo "启动 Wenshi 表型控制台..."
  python3 -m wenshi_patrol.phenotype_controller --config "$CONFIG" --runtime-root "$ROOT/runtime/runs" 2>&1 \
    | tee -a "$WENSHI_RUN_DIR/console.log"
  exit ${PIPESTATUS[0]}
fi

echo "启动 Wenshi 中文控制台..."
python3 -m wenshi_patrol.patrol_controller --config "$CONFIG" 2>&1 \
  | tee -a "$WENSHI_RUN_DIR/console.log"
