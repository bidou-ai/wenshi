"""Optional Ultralytics training entry point; never publishes automatically."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


def run_training(args: argparse.Namespace) -> int:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("缺少 ultralytics；请先安装 requirements-ubuntu.txt 中的训练依赖") from exc
    model = YOLO(args.base_model)
    model.train(data=str(args.data), epochs=int(args.epochs), imgsz=int(args.imgsz), device=args.device, project=str(args.project), name=args.name)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="训练 Wenshi rice/flower YOLO 模型，不自动发布")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base-model", default="yolo11n.pt")
    parser.add_argument("--project", type=Path, default=Path("yubei/training"))
    parser.add_argument("--name", default=datetime.now().strftime("rice_%Y%m%d_%H%M%S"))
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)
    return run_training(args)


if __name__ == "__main__":
    raise SystemExit(main())

