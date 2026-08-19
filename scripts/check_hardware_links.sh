#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${WENSHI_CONFIG:-$ROOT/config/wenshi.yaml}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"

python3 -m wenshi_patrol.hardware_check --config "$CONFIG" "$@"

