#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${WENSHI_DATA_ROOT:-$ROOT/yubei/data}"
STAGED_VIEWPOINTS="${WENSHI_STAGED_VIEWPOINTS:-$ROOT/yubei/viewpoints_staged.json}"

usage() {
  cat <<'EOF'
Wenshi yubei 预备工具

用法: ./yubei/start_yubei.sh [命令] [参数]

不带命令时显示中文菜单。可用命令：
  check                 只读检查 AGV、JAKA 和 D435
  camera-check          只检查 D435，不连接或控制 AGV/JAKA
  capture [--focus flower]
                        回车采集 RGB；可默认标记为开花批次
  audit [会话目录]      检查照片清晰度、曝光、重复图和采集批次
  label [会话目录]      启动本地标注网页，默认使用最新会话
  package-labeler [会话目录] [输出目录]
                        打包可复制到 Windows 的离线标注文件夹
  prepare [会话目录]    验证并生成可训练的 train/val 数据集
  train [data.yaml]     启动 YOLO 训练
  teach                 只读依次保存八个 JAKA 示教点（现场动作测试使用 start_field_test.sh）
  verify                校验暂存的八点示教文件
  publish-viewpoints --confirm
                        备份并发布已验证的八点示教文件
  publish-model [best.pt] --confirm
                        备份并发布模型，默认选择最新 best.pt

所有相对路径都按项目根目录解析，脚本可从任意当前目录启动。
EOF
}

latest_session() {
  [[ -d "$DATA_ROOT" ]] || return 0
  find "$DATA_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'dataset_*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | sed -n '1p' | cut -d' ' -f2-
}

latest_prepared() {
  [[ -d "$ROOT/yubei/datasets" ]] || return 0
  find "$ROOT/yubei/datasets" -mindepth 2 -maxdepth 2 -type f -name data.yaml -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | sed -n '1p' | cut -d' ' -f2-
}

latest_model() {
  [[ -d "$ROOT/yubei/training" ]] || return 0
  find "$ROOT/yubei/training" -type f -path '*/weights/best.pt' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | sed -n '1p' | cut -d' ' -f2-
}

run_command() {
  local command="${1:-}"
  shift || true
  cd "$ROOT"
  case "$command" in
  check)
      python3 yubei/check_all.py --config "${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}" --samples 10
      return $?
      ;;
    camera-check)
      python3 yubei/camera_only.py --config "${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}" --samples 10
      return $?
      ;;
    capture)
      python3 yubei/dataset_capture.py \
        --config "${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}" \
        --output "$DATA_ROOT" --preview "$@"
      ;;
    audit)
      local session="${1:-$(latest_session)}"
      [[ -n "$session" ]] || { echo "没有找到数据集会话" >&2; return 1; }
      python3 yubei/capture_audit.py "$session"
      ;;
    label)
      local session="${1:-$(latest_session)}"
      [[ -n "$session" ]] || { echo "没有找到数据集会话" >&2; return 1; }
      python3 yubei/label_server.py --session "$session" --open-browser
      ;;
    package-labeler)
      local session="${1:-$(latest_session)}"
      [[ -n "$session" ]] || { echo "没有找到数据集会话" >&2; return 1; }
      local output="${2:-$ROOT/yubei/windows_labeler_$(basename "$session")}"
      python3 yubei/package_labeler.py "$session" --output "$output"
      ;;
    prepare)
      local session="${1:-$(latest_session)}"
      [[ -n "$session" ]] || { echo "没有找到数据集会话" >&2; return 1; }
      local output="$ROOT/yubei/datasets/$(basename "$session")_$(date +%Y%m%d_%H%M%S_%N)"
      python3 yubei/dataset_validate.py "$session" --prepare "$output"
      ;;
    train)
      local data="${1:-$(latest_prepared)}"
      [[ -n "$data" ]] || { echo "没有找到已准备的 data.yaml，请先运行 prepare" >&2; return 1; }
      python3 yubei/train_yolo.py --data "$data" --project "$ROOT/yubei/training" --device cpu
      ;;
    teach)
      python3 yubei/teach_viewpoints.py --output "$STAGED_VIEWPOINTS" --all
      ;;
    verify)
      python3 yubei/viewpoint_verify.py "$STAGED_VIEWPOINTS"
      ;;
    publish-viewpoints)
      [[ "${1:-}" == "--confirm" ]] || {
        echo "发布示教点需要显式添加 --confirm" >&2
        return 2
      }
      python3 yubei/viewpoint_verify.py "$STAGED_VIEWPOINTS" \
        --publish-to "$ROOT/config/viewpoints.json" \
        --backup-dir "$ROOT/yubei/backups" \
        --confirm
      ;;
    publish-model)
      local source=""
      local confirmation=""
      if [[ "${1:-}" == "--confirm" ]]; then
        source="$(latest_model)"
        confirmation="--confirm"
      else
        source="${1:-$(latest_model)}"
        confirmation="${2:-}"
      fi
      [[ "$confirmation" == "--confirm" ]] || {
        echo "发布模型需要显式添加 --confirm" >&2
        return 2
      }
      [[ -n "$source" ]] || { echo "没有找到 best.pt" >&2; return 1; }
      python3 yubei/publish_model.py "$source" --models "$ROOT/models"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      echo "未知命令: $command" >&2
      usage >&2
      return 2
      ;;
  esac
}

if [[ $# -gt 0 ]]; then
  run_command "$@"
  exit $?
fi

while true; do
  cat <<'EOF'

Wenshi yubei 预备工具
  1. 设备与相机只读检查
  2. 只检查 D435 相机
  3. 回车采集 RGB 数据集
  4. 检查最新照片质量与重复图
  5. 标注最新数据集
  6. 打包 Windows 离线标注文件夹
  7. 验证并生成训练数据集
  8. 训练 YOLO 模型
  9. 依次保存八个示教点
  10. 校验示教点
  11. 发布已验证的示教点
  12. 发布已确认的模型
  q. 退出
EOF
  read -r -p "请选择: " choice
  case "$choice" in
    1) run_command check || true ;;
    2) run_command camera-check || true ;;
    3) run_command capture ;;
    4) run_command audit || true ;;
    5) run_command label ;;
    6) run_command package-labeler ;;
    7) run_command prepare ;;
    8) run_command train ;;
    9) run_command teach ;;
    10) run_command verify ;;
    11)
      read -r -p "输入 PUBLISH 确认覆盖正式示教文件（会先备份）: " confirmation
      [[ "$confirmation" == "PUBLISH" ]] && run_command publish-viewpoints --confirm || echo "已取消"
      ;;
    12)
      read -r -p "输入 PUBLISH 确认发布最新 best.pt（会先备份）: " confirmation
      [[ "$confirmation" == "PUBLISH" ]] && run_command publish-model --confirm || echo "已取消"
      ;;
    q|Q) exit 0 ;;
    *) echo "请输入 1-12 或 q" ;;
  esac
done
