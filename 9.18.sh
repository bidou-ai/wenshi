#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="${WENSHI_918_RUNTIME:-$ROOT/runtime/9.18}"
CONFIG="${WENSHI_918_CONFIG:-$ROOT/config/9.18.yaml}"
SETUP_FILE="${WENSHI_918_SETUP:-}"
PHOTOS="${WENSHI_918_PHOTOS:-}"
NO_RVIZ=0
NO_CAMERA=0
DRY_RUN=0
SETUP_MODE=0
MAP_SOURCE=""
RESUME_DIR=""
REUSE_VIEWPOINTS=""

usage() {
  cat <<'EOF'
用法: ./9.18.sh [选项]

无 Tag 新场地专家展示：使用现场扫描地图和动态示教路线，AGV 每圈随机观察
3-5 个水稻点（不足 3 个时全部观察），RViz 显示地图、路线、AGV 和 D435。
不运行 YOLO、不做监测；仅在控制台输入 photo 时保存照片。

首次现场配置:
  ./9.18.sh --setup --map /path/to/new-site.smap

选项:
  --config PATH             使用指定的独立配置
  --runtime-root PATH       使用指定的 9.18 独立运行目录
  --setup                   交互登记路线锚点、观察点和机械臂姿态
  --map PATH                新建 setup 使用的扫描 .smap
  --resume DIR              从 9.18 setup 证据目录继续登记
  --reuse-viewpoints PATH   一次性复制已有 home_safe/left/right 姿态
  --setup-file PATH         9.18 独立 setup JSON
  --photos PATH             photo 命令保存 JPEG 的目录
  --no-rviz                 不启动 RViz
  --no-camera               不启动 D435 桥；setup 时需输入已有图片路径
  --dry-run                 只校验本地配置，不连接任何硬件
  -h, --help                显示帮助
EOF
}

need_value() {
  if [[ $# -lt 2 || -z "${2:-}" ]]; then
    echo "错误：$1 需要一个路径参数" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) need_value "$@"; CONFIG="$2"; shift 2 ;;
    --runtime-root) need_value "$@"; RUNTIME_ROOT="$2"; shift 2 ;;
    --setup) SETUP_MODE=1; shift ;;
    --map) need_value "$@"; MAP_SOURCE="$2"; shift 2 ;;
    --resume) need_value "$@"; RESUME_DIR="$2"; shift 2 ;;
    --reuse-viewpoints) need_value "$@"; REUSE_VIEWPOINTS="$2"; shift 2 ;;
    --setup-file) need_value "$@"; SETUP_FILE="$2"; shift 2 ;;
    --photos) need_value "$@"; PHOTOS="$2"; shift 2 ;;
    --no-rviz) NO_RVIZ=1; shift ;;
    --no-camera) NO_CAMERA=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SETUP_FILE="${SETUP_FILE:-$RUNTIME_ROOT/demo_setup.json}"
PHOTOS="${PHOTOS:-$RUNTIME_ROOT/photos}"

if [[ "$SETUP_MODE" == 1 && "$DRY_RUN" == 1 ]]; then
  echo "错误：--setup 与 --dry-run 不能同时使用" >&2
  exit 2
fi
if [[ -n "$MAP_SOURCE" && "$SETUP_MODE" != 1 ]]; then
  echo "错误：--map 只能与 --setup 一起使用" >&2
  exit 2
fi
if [[ -n "$RESUME_DIR" && "$SETUP_MODE" != 1 ]]; then
  echo "错误：--resume 只能与 --setup 一起使用" >&2
  exit 2
fi
if [[ -n "$REUSE_VIEWPOINTS" && "$SETUP_MODE" != 1 ]]; then
  echo "错误：--reuse-viewpoints 只能与 --setup 一起使用" >&2
  exit 2
fi
if [[ -n "$RESUME_DIR" && -n "$MAP_SOURCE" ]]; then
  echo "错误：--resume 与 --map 不能同时使用" >&2
  exit 2
fi
if [[ "$SETUP_MODE" == 1 && -z "$RESUME_DIR" && -z "$MAP_SOURCE" ]]; then
  echo "错误：新建 setup 必须指定 --map PATH" >&2
  exit 2
fi

RUNTIME_ROOT="$(realpath -m "$RUNTIME_ROOT")"
FORMAL_RUNTIME="$(realpath -m "$ROOT/runtime/height_tests")"
LEGACY_RUNTIME_NAME="demo"
LEGACY_RUNTIME="$(realpath -m "$ROOT/runtime/$LEGACY_RUNTIME_NAME")"
case "$RUNTIME_ROOT" in
  "$FORMAL_RUNTIME"|"$FORMAL_RUNTIME"/*|"$LEGACY_RUNTIME"|"$LEGACY_RUNTIME"/*)
    echo "错误：9.18 必须使用自己的独立运行目录，不能复用旧 Demo 或正式检测目录" >&2
    exit 2
    ;;
esac

reject_reserved_runtime_path() {
  local label="$1"
  local candidate
  candidate="$(realpath -m "$2")"
  case "$candidate" in
    "$FORMAL_RUNTIME"|"$FORMAL_RUNTIME"/*|"$LEGACY_RUNTIME"|"$LEGACY_RUNTIME"/*)
      echo "错误：$label 不能使用旧 Demo 或正式检测路径，必须位于 9.18 独立目录" >&2
      exit 2
      ;;
  esac
}
reject_reserved_runtime_path "setup" "$SETUP_FILE"
reject_reserved_runtime_path "照片" "$PHOTOS"
if [[ -n "$RESUME_DIR" ]]; then
  reject_reserved_runtime_path "恢复目录" "$RESUME_DIR"
fi

if [[ "$SETUP_MODE" != 1 && ! -f "$SETUP_FILE" ]]; then
  echo "错误：缺少 9.18 独立 setup $SETUP_FILE。先运行 ./9.18.sh --setup --map PATH" >&2
  exit 2
fi

require_918_runtime_path() {
  local label="$1"
  local candidate
  candidate="$(realpath -m "$2")"
  case "$candidate" in
    "$RUNTIME_ROOT"|"$RUNTIME_ROOT"/*) ;;
    *)
      echo "错误：$label 必须位于 9.18 独立目录 $RUNTIME_ROOT" >&2
      exit 2
      ;;
  esac
}
require_918_runtime_path "setup" "$SETUP_FILE"
require_918_runtime_path "照片" "$PHOTOS"
if [[ -n "$RESUME_DIR" ]]; then
  require_918_runtime_path "恢复目录" "$RESUME_DIR"
fi

acquire_hardware_lock() {
  local lock_file="${WENSHI_918_LOCK:-${TMPDIR:-/tmp}/wenshi-demo-hardware.lock}"
  if ! command -v flock >/dev/null 2>&1; then
    echo "错误：缺少 flock，无法保证 Demo 独占 AGV/JAKA" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$lock_file")"
  exec 9>"$lock_file"
  if ! flock -n 9; then
    echo "错误：另一个 Wenshi Demo 正在占用 AGV/JAKA，请先结束已有进程" >&2
    exit 2
  fi
}

if [[ "$SETUP_MODE" == 1 ]]; then
  acquire_hardware_lock
  SETUP_ARGS=(
    --config "$CONFIG"
    --runtime-root "$RUNTIME_ROOT"
    --setup-file "$SETUP_FILE"
    --setup
  )
  if [[ -n "$MAP_SOURCE" ]]; then
    SETUP_ARGS+=(--map "$MAP_SOURCE")
  fi
  if [[ -n "$RESUME_DIR" ]]; then
    SETUP_ARGS+=(--resume "$RESUME_DIR")
  fi
  if [[ -n "$REUSE_VIEWPOINTS" ]]; then
    SETUP_ARGS+=(--reuse-viewpoints "$REUSE_VIEWPOINTS")
  fi
  if [[ "$NO_CAMERA" == 1 ]]; then
    SETUP_ARGS+=(--no-camera)
  fi
  PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m wenshi_patrol.demo_918 "${SETUP_ARGS[@]}"
  exit $?
fi

if [[ "$DRY_RUN" == 1 ]]; then
  PLAN_ARGS=(
    --config "$CONFIG"
    --runtime-root "$RUNTIME_ROOT"
    --photos "$PHOTOS"
    --setup-file "$SETUP_FILE"
    --dry-run
  )
  if [[ "$NO_CAMERA" == 1 ]]; then
    PLAN_ARGS+=(--no-camera)
  fi
  PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m wenshi_patrol.demo_918 "${PLAN_ARGS[@]}"
  echo "dry-run: no hardware connection, no ROS process, no monitoring"
  echo "config=$CONFIG"
  echo "runtime=$RUNTIME_ROOT"
  echo "photos=$PHOTOS"
  echo "setup=$SETUP_FILE"
  echo "camera_bridge=$([[ "$NO_CAMERA" == 0 ]] && echo enabled || echo disabled)"
  echo "rviz=$([[ "$NO_RVIZ" == 0 ]] && echo enabled || echo disabled)"
  exit 0
fi

acquire_hardware_lock

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

DEMO_TMP="${TMPDIR:-/tmp}/wenshi-918-demo-$$-$(date +%Y%m%d_%H%M%S)"
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
if python3 -c 'import rclpy' >/dev/null 2>&1; then
  ROS_PYTHON=1
fi
if [[ "$NO_RVIZ" == 0 && "$ROS_PYTHON" != 1 ]]; then
  echo "错误：默认 9.18 展示需要 ROS2 Python；无界面运行请显式使用 --no-rviz" >&2
  exit 2
fi
if [[ "$NO_RVIZ" == 0 ]] && ! command -v rviz2 >/dev/null 2>&1; then
  echo "错误：默认 9.18 展示需要 rviz2；无界面运行请显式使用 --no-rviz" >&2
  exit 2
fi

if [[ "$NO_CAMERA" == 0 && "$ROS_PYTHON" == 1 ]]; then
  echo "启动 9.18 D435 ROS2 桥（临时日志: $DEMO_TMP/camera_bridge.log）"
  python3 -m wenshi_patrol.camera_bridge --config "$CONFIG" >"$DEMO_TMP/camera_bridge.log" 2>&1 &
  PIDS+=("$!")
  sleep 0.3
  if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo "错误：9.18 D435 ROS2 桥启动失败，详见 $DEMO_TMP/camera_bridge.log" >&2
    exit 1
  fi
elif [[ "$NO_CAMERA" == 0 ]]; then
  echo "警告：当前 Python 没有 rclpy，D435 ROS2 桥未启动"
fi

if [[ "$NO_RVIZ" == 0 ]]; then
  echo "启动 9.18 RViz 新地图与 D435 画面"
  rviz2 -d "$ROOT/config/9.18.rviz" >"$DEMO_TMP/rviz.log" 2>&1 &
  PIDS+=("$!")
  sleep 0.3
  if ! kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo "错误：9.18 RViz 启动失败，详见 $DEMO_TMP/rviz.log" >&2
    exit 1
  fi
fi

echo "启动 9.18 无Tag专家展示。照片只在输入 photo 后写入: $PHOTOS"
DEMO_ARGS=(
  --config "$CONFIG"
  --runtime-root "$RUNTIME_ROOT"
  --photos "$PHOTOS"
  --setup-file "$SETUP_FILE"
)
if [[ "$NO_RVIZ" == 1 ]]; then
  DEMO_ARGS+=(--no-rviz)
fi
if [[ "$NO_CAMERA" == 1 ]]; then
  DEMO_ARGS+=(--no-camera)
fi
python3 -m wenshi_patrol.demo_918 "${DEMO_ARGS[@]}"
