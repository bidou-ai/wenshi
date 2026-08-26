#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}"
OUTPUT="${WENSHI_FIELD_TEST_ROOT:-$ROOT/runtime/field_tests}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
用法: ./scripts/start_field_test.sh [--no-rviz] [--no-preview]

默认启动 D435 ROS2 桥、RViz 和现场测试控制台；--no-rviz 只运行硬件测试控制台。
进入 field> 后输入 teach、test route、test arm、status、stop 或 q。
EOF
  exit 0
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "配置文件不存在: $CONFIG" >&2
  exit 1
fi

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT/app:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
export no_proxy="${no_proxy:+$no_proxy,}192.168.192.5,192.168.192.160,192.168.192.203"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}192.168.192.5,192.168.192.160,192.168.192.203"

cd "$ROOT"

NO_RVIZ=0
FIELD_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--no-rviz" ]]; then
    NO_RVIZ=1
  else
    FIELD_ARGS+=("$arg")
  fi
done

PIDS=()
cleanup() {
  set +e
  for pid in "${PIDS[@]:-}"; do
    kill -INT "$pid" 2>/dev/null || true
  done
  wait "${PIDS[@]:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$NO_RVIZ" -eq 0 ]]; then
  if python3 -c 'import rclpy' >/dev/null 2>&1; then
    ROS_LOG_ROOT="$OUTPUT/field_test_ros_$(date +%Y%m%d_%H%M%S_%N)"
    mkdir -p "$ROS_LOG_ROOT"
    export WENSHI_RUN_DIR="$ROS_LOG_ROOT"
    echo "启动 D435 ROS2 相机桥，日志: $ROS_LOG_ROOT/camera.log"
    python3 -m wenshi_patrol.camera_bridge --config "$CONFIG" \
      >"$ROS_LOG_ROOT/camera_console.log" 2>&1 &
    PIDS+=("$!")
    if command -v rviz2 >/dev/null 2>&1; then
      echo "启动 RViz 地图与相机画面，日志: $ROS_LOG_ROOT/rviz.log"
      rviz2 -d "$ROOT/config/wenshi.rviz" >"$ROS_LOG_ROOT/rviz.log" 2>&1 &
      PIDS+=("$!")
    else
      echo "提示: 未找到 rviz2，仍会发布地图/位姿 ROS 话题。"
    fi
    FIELD_ARGS+=("--ros")
  else
    echo "提示: 当前 ROS2 Python 环境不可用，跳过 RViz/相机桥；可用 --no-rviz 运行纯硬件测试。"
  fi
fi

python3 "$ROOT/yubei/field_test.py" --config "$CONFIG" --output "$OUTPUT" "${FIELD_ARGS[@]}"
