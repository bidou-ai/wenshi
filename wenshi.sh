#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}"
PHOTOS="${WENSHI_DEMO_PHOTOS:-$ROOT/runtime/demo}"
VIEWPOINTS="${WENSHI_DEMO_VIEWPOINTS:-}"
NO_RVIZ=0
NO_CAMERA=0
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: ./wenshi.sh [选项]

专家展示模式：AGV 按现有 wens1 路线循环，JAKA 在站点做三视角观察，
RViz 显示地图/AGV 位姿/路线标记/D435画面。不运行 YOLO、不做株高监测、
不写 runtime/runs。控制台命令：pause、start、photo、status、stop、q。

选项:
  --config PATH       使用指定配置
  --photos PATH       photo 命令保存 JPEG 的目录（默认 runtime/demo）
  --viewpoints PATH   必须是包含 16 个水稻停车点的完整 field_height_setup.json
  --no-rviz           不启动 RViz，仍可运行展示控制台
  --no-camera         不启动 D435 ROS2 桥
  --dry-run           只打印启动计划，不连接硬件
  -h, --help          显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --photos) PHOTOS="$2"; shift 2 ;;
    --viewpoints) VIEWPOINTS="$2"; shift 2 ;;
    --no-rviz) NO_RVIZ=1; shift ;;
    --no-camera) NO_CAMERA=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
done

select_viewpoints() {
  if [[ -n "$VIEWPOINTS" ]]; then
    return
  fi
  if [[ -f "$ROOT/runtime/height_tests/field_height_setup.json" ]]; then
    VIEWPOINTS="$ROOT/runtime/height_tests/field_height_setup.json"
  fi
}

select_viewpoints

if [[ -z "$VIEWPOINTS" ]]; then
  echo "错误：缺少16个水稻观测停车点的完整 setup。先运行 ./scripts/start_height_test.sh setup --interactive" >&2
  exit 2
fi

if [[ "$DRY_RUN" == 1 ]]; then
  PLAN_ARGS=(--config "$CONFIG" --photos "$PHOTOS" --dry-run)
  if [[ -n "$VIEWPOINTS" ]]; then
    PLAN_ARGS+=(--viewpoints "$VIEWPOINTS")
  fi
  if [[ "$NO_CAMERA" == 1 ]]; then
    PLAN_ARGS+=(--no-camera)
  fi
  PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}" python3 -m wenshi_patrol.demo "${PLAN_ARGS[@]}"
  echo "dry-run: no hardware connection, no ROS process, no monitoring"
  echo "config=$CONFIG"
  echo "photos=$PHOTOS"
  echo "viewpoints=${VIEWPOINTS:-missing}"
  echo "camera_bridge=$([[ "$NO_CAMERA" == 0 ]] && echo enabled || echo disabled)"
  echo "rviz=$([[ "$NO_RVIZ" == 0 ]] && echo enabled || echo disabled)"
  exit 0
fi

export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
export no_proxy="${no_proxy:+$no_proxy,}192.168.192.5,192.168.192.160,192.168.192.203"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}192.168.192.5,192.168.192.160,192.168.192.203"

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
fi

DEMO_TMP="${TMPDIR:-/tmp}/wenshi-demo-$$-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DEMO_TMP"
export WENSHI_RUN_DIR="$DEMO_TMP"
PIDS=()
cleanup() {
  set +e
  for pid in "${PIDS[@]:-}"; do
    kill -INT "$pid" 2>/dev/null || true
  done
  sleep 0.2
  for pid in "${PIDS[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait "${PIDS[@]:-}" 2>/dev/null || true
  rm -rf "$DEMO_TMP"
}
trap cleanup EXIT INT TERM

ROS_PYTHON=0
if python3 -c 'import rclpy' >/dev/null 2>&1; then ROS_PYTHON=1; fi

if [[ "$NO_RVIZ" == 0 ]]; then
  if [[ "$ROS_PYTHON" != 1 ]]; then
    echo "错误：默认专家展示需要可用 ROS2 Python；无显示演示请显式使用 --no-rviz。" >&2
    exit 2
  fi
  if ! command -v rviz2 >/dev/null 2>&1; then
    echo "错误：默认专家展示需要 rviz2；无显示演示请显式使用 --no-rviz。" >&2
    exit 2
  fi
fi

if [[ "$NO_CAMERA" == 0 && "$ROS_PYTHON" == 1 ]]; then
  echo "启动 D435 ROS2 桥（临时日志: $DEMO_TMP/camera_bridge.log）"
  python3 -m wenshi_patrol.camera_bridge --config "$CONFIG" >"$DEMO_TMP/camera_bridge.log" 2>&1 &
  PIDS+=("$!")
  sleep 0.3
  if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo "错误：D435 ROS2 桥启动失败，详见 $DEMO_TMP/camera_bridge.log" >&2
    exit 1
  fi
elif [[ "$NO_CAMERA" == 0 ]]; then
  echo "警告：当前 Python 没有 rclpy，跳过 D435 ROS2 桥；展示控制台仍会检查 D435 HTTP 服务。"
fi

if [[ "$NO_RVIZ" == 0 && "$ROS_PYTHON" == 1 && $(command -v rviz2 >/dev/null 2>&1; echo $?) == 0 ]]; then
  echo "启动 RViz 地图与 D435 画面"
  rviz2 -d "$ROOT/config/wenshi.rviz" >"$DEMO_TMP/rviz.log" 2>&1 &
  PIDS+=("$!")
  sleep 0.3
  if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo "错误：RViz 启动失败，详见 $DEMO_TMP/rviz.log" >&2
    exit 1
  fi
elif [[ "$NO_RVIZ" == 0 ]]; then
  echo "警告：未找到可用 rviz2，跳过 RViz；展示控制台仍可运行。"
fi

echo "启动 Wenshi 专家展示。照片只在输入 photo 后写入: $PHOTOS"
DEMO_ARGS=(--config "$CONFIG" --photos "$PHOTOS")
if [[ "$NO_RVIZ" == 1 ]]; then
  DEMO_ARGS+=(--no-rviz)
fi
if [[ -n "$VIEWPOINTS" ]]; then
  DEMO_ARGS+=(--viewpoints "$VIEWPOINTS")
fi
if [[ "$NO_CAMERA" == 1 ]]; then
  DEMO_ARGS+=(--no-camera)
fi
python3 -m wenshi_patrol.demo "${DEMO_ARGS[@]}"
