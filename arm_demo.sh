#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$ROOT/config/arm_demo.yaml"
RUNTIME_ROOT="$ROOT/runtime/arm_demo"
SETUP_FILE=""
PHOTOS=""
SETUP_MODE=0
RESUME_DIR=""
DRY_RUN=0
TEST_ONCE=0

usage() {
  cat <<'EOF'
用法: ./arm_demo.sh [选项]

固定底盘的双株稻穗候选框展示，只连接 JAKA 与 D435。

  --setup                 交互示教 10 个机械臂姿态
  --resume DIR            从独立 setup 证据目录继续示教（需配合 --setup）
  --dry-run               只校验配置、模型和 setup，不连接硬件或创建窗口
  --test-once             全程低速完成一轮后停在 home_safe
  --config PATH           使用指定配置文件
  --runtime-root PATH     使用指定独立运行目录
  --setup-file PATH       使用指定 demo_setup.json
  --photos PATH           指定 P 键截图目录
  -h, --help              显示帮助
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
    --setup-file) need_value "$@"; SETUP_FILE="$2"; shift 2 ;;
    --photos) need_value "$@"; PHOTOS="$2"; shift 2 ;;
    --setup) SETUP_MODE=1; shift ;;
    --resume) need_value "$@"; RESUME_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --test-once) TEST_ONCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "$RESUME_DIR" && "$SETUP_MODE" != 1 ]]; then
  echo "错误：--resume 只能与 --setup 一起使用" >&2
  exit 2
fi
if [[ "$SETUP_MODE" == 1 && ( "$DRY_RUN" == 1 || "$TEST_ONCE" == 1 ) ]]; then
  echo "错误：--setup 不能与 --dry-run/--test-once 同时使用" >&2
  exit 2
fi
if [[ "$DRY_RUN" == 1 && "$TEST_ONCE" == 1 ]]; then
  echo "错误：--dry-run 与 --test-once 不能同时使用" >&2
  exit 2
fi

RUNTIME_ROOT="$(realpath -m "$RUNTIME_ROOT")"
SETUP_FILE="$(realpath -m "${SETUP_FILE:-$RUNTIME_ROOT/demo_setup.json}")"
PHOTOS="$(realpath -m "${PHOTOS:-$RUNTIME_ROOT/photos}")"
if [[ -n "$RESUME_DIR" ]]; then
  RESUME_DIR="$(realpath -m "$RESUME_DIR")"
fi

for reserved_name in demo 9.18 height_tests; do
  reserved_root="$ROOT/runtime/$reserved_name"
  case "$RUNTIME_ROOT" in
    "$reserved_root"|"$reserved_root"/*)
      echo "错误：运行目录必须是 arm_demo 的独立目录" >&2
      exit 2
      ;;
  esac
done

require_runtime_path() {
  local label="$1"
  local candidate="$2"
  case "$candidate" in
    "$RUNTIME_ROOT"|"$RUNTIME_ROOT"/*) ;;
    *)
      echo "错误：$label 必须位于独立运行目录 $RUNTIME_ROOT" >&2
      exit 2
      ;;
  esac
}

require_runtime_path "setup" "$SETUP_FILE"
require_runtime_path "照片" "$PHOTOS"
if [[ -n "$RESUME_DIR" ]]; then
  require_runtime_path "恢复目录" "$RESUME_DIR"
fi
if [[ "$SETUP_MODE" != 1 && ! -f "$SETUP_FILE" ]]; then
  echo "错误：缺少独立 setup $SETUP_FILE；请先运行 ./arm_demo.sh --setup" >&2
  exit 2
fi

CLI_ARGS=(
  --config "$CONFIG"
  --runtime-root "$RUNTIME_ROOT"
  --setup-file "$SETUP_FILE"
  --photos "$PHOTOS"
)
if [[ "$SETUP_MODE" == 1 ]]; then
  CLI_ARGS+=(--setup)
fi
if [[ -n "$RESUME_DIR" ]]; then
  CLI_ARGS+=(--resume "$RESUME_DIR")
fi
if [[ "$DRY_RUN" == 1 ]]; then
  CLI_ARGS+=(--dry-run)
fi
if [[ "$TEST_ONCE" == 1 ]]; then
  CLI_ARGS+=(--test-once)
fi

export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export no_proxy="192.168.192.160,192.168.192.203"
export NO_PROXY="$no_proxy"

if [[ "$DRY_RUN" != 1 ]]; then
  HARDWARE_LOCK="${TMPDIR:-/tmp}/wenshi-demo-hardware.lock"
  if ! command -v flock >/dev/null 2>&1; then
    echo "错误：缺少 flock，无法独占机械臂硬件" >&2
    exit 2
  fi
  exec 9>"$HARDWARE_LOCK"
  if ! flock -n 9; then
    echo "错误：另一个展示正在占用机械臂硬件" >&2
    exit 2
  fi
fi

exec python3 -m wenshi_patrol.arm_demo "${CLI_ARGS[@]}"
