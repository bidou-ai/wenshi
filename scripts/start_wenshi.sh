#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}"

if [[ -f /opt/ros/humble/setup.bash ]]; then
  source /opt/ros/humble/setup.bash
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
export no_proxy="${no_proxy:+$no_proxy,}192.168.192.5,192.168.192.160,192.168.192.203"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}192.168.192.5,192.168.192.160,192.168.192.203"

"$ROOT/scripts/check_environment.sh"

STAMP="$(date +%Y%m%d_%H%M%S)"
export WENSHI_RUN_DIR="$ROOT/runtime/run_$STAMP"
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
echo "启动 D435 ROS2 桥..."
python3 -m wenshi_patrol.camera_bridge --config "$CONFIG" \
  >"$WENSHI_RUN_DIR/camera_console.log" 2>&1 &
PIDS+=("$!")

if command -v rviz2 >/dev/null 2>&1; then
  echo "启动 RViz..."
  rviz2 -d "$ROOT/config/wenshi.rviz" >"$WENSHI_RUN_DIR/rviz.log" 2>&1 &
  PIDS+=("$!")
fi

echo "启动 Wenshi 中文控制台..."
python3 -m wenshi_patrol.patrol_controller --config "$CONFIG"

