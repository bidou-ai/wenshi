"""Optional Ultralytics training entry point; never publishes automatically."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import yaml


def run_training(args: argparse.Namespace) -> int:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("缺少 ultralytics；请先安装 requirements-ubuntu.txt 中的训练依赖") from exc
    model = YOLO(args.base_model)
    model.train(data=str(args.data), epochs=int(args.epochs), imgsz=int(args.imgsz), device=args.device, project=str(args.project), name=args.name)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="训练 Wenshi 单类别 YOLO 模型，不自动发布")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base-model", default="yolo11n.pt")
    parser.add_argument("--project", type=Path, default=Path("yubei/training"))
    parser.add_argument("--name", default=datetime.now().strftime("rice_%Y%m%d_%H%M%S"))
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model-type", choices=("plant", "panicle"))
    args = parser.parse_args(argv)
    try:
        if args.model_type:
            value = yaml.safe_load(args.data.read_text(encoding="utf-8")) or {}
            names = value.get("names", {})
            actual_names = set(names.values()) if isinstance(names, dict) else set(names)
            expected_name = "rice_plant" if args.model_type == "plant" else "panicle"
            if actual_names != {expected_name}:
                raise RuntimeError(f"模型类别不匹配：{args.model_type} 需要 {expected_name}")
        return run_training(args)
    except (OSError, yaml.YAMLError, RuntimeError) as exc:
        print(f"训练未启动: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
