"""Create a rotated, training-ready copy of a raw RGB dataset session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import cv2


def normalize_session(source: Path, output: Path, rotation: str = "clockwise_90") -> Path:
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if rotation not in {"clockwise_90", "none"}:
        raise ValueError("rotation must be clockwise_90 or none")
    if not (source / "images").is_dir():
        raise ValueError("source session must contain images/")
    if rotation != "none" and (source / "labels").is_dir():
        labelled_files = list((source / "labels").rglob("*.txt"))
        if labelled_files:
            raise ValueError("labels before rotation; normalize before annotation")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("labels", "ambiguous"):
        (output / name).mkdir()
    for image_path in sorted((source / "images").rglob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f"cannot decode image: {image_path.name}")
        relative = image_path.relative_to(source / "images")
        destination = output / "images" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE) if rotation == "clockwise_90" else image
        if not cv2.imwrite(str(destination), rotated):
            raise OSError(f"failed to write image: {destination}")
    for name in ("labels", "ambiguous"):
        source_dir = source / name
        if source_dir.is_dir():
            for item in source_dir.rglob("*"):
                if item.is_file():
                    destination = output / name / item.relative_to(source_dir)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, destination)
    manifest_path = source / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"images": []}
    manifest["normalization"] = {"rotation": rotation, "source": str(source)}
    for item in manifest.get("images", []):
        metrics = item.get("quality", {}).get("metrics", {})
        if metrics.get("width") is not None and metrics.get("height") is not None:
            metrics["width"], metrics["height"] = metrics["height"], metrics["width"]
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按数据集类型规范化训练图片方向")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rotation", choices=("clockwise_90", "none"), default="clockwise_90")
    args = parser.parse_args(argv)
    try:
        print(normalize_session(args.source, args.output, args.rotation))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"规范化失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
