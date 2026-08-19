#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"

test -f "$CONFIG"
test -f "$ROOT/map/wenshi.smap"
test -f "$ROOT/config/viewpoints.json"
python3 -c 'import cv2, numpy, yaml'
python3 -m wenshi_patrol.project_check --config "$CONFIG"
python3 -c 'import rclpy; import sensor_msgs.msg; import nav_msgs.msg; import geometry_msgs.msg; import visualization_msgs.msg'

echo "[OK] ROS2、Python 依赖和 Wenshi 离线配置预检通过"

