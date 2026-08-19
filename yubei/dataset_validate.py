"""Offline validation and deterministic split for YOLO datasets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import random
from typing import Any

import cv2


@dataclass
class ValidationReport:
    ok: bool
    image_count: int
    label_count: int
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "image_count": self.image_count, "label_count": self.label_count, "issues": self.issues}


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _parse_line(line: str, path: Path, number: int) -> tuple[int, list[float]] | None:
    parts = line.split()
    if len(parts) != 5:
        raise ValueError(f"{path}:{number}: YOLO line needs 5 fields")
    try:
        class_id = int(parts[0])
        values = [float(item) for item in parts[1:]]
    except ValueError as exc:
        raise ValueError(f"{path}:{number}: values are not numeric") from exc
    if class_id not in {0, 1}:
        raise ValueError(f"{path}:{number}: class id is not 0 or 1")
    if any(value < 0.0 or value > 1.0 for value in values):
        raise ValueError(f"{path}:{number}: box values outside [0,1] range")
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError(f"{path}:{number}: box width/height must be positive")
    return class_id, values


def validate_dataset(session: Path) -> ValidationReport:
    root = Path(session).expanduser().resolve()
    images_dir = root / "images"
    labels_dir = root / "labels"
    issues: list[str] = []
    images = sorted(path for path in images_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES) if images_dir.is_dir() else []
    if not images_dir.is_dir():
        issues.append("missing images directory")
    if not labels_dir.is_dir():
        issues.append("missing labels directory")
    label_count = 0
    for image in images:
        loaded = cv2.imread(str(image), cv2.IMREAD_UNCHANGED)
        if loaded is None:
            issues.append(f"cannot decode image: {image.name}")
        label_path = labels_dir / f"{image.stem}.txt"
        if not label_path.exists():
            issues.append(f"missing label: {label_path.name}")
            continue
        label_count += 1
        for number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                _parse_line(line, label_path, number)
            except ValueError as exc:
                issues.append(str(exc))
    return ValidationReport(not issues, len(images), label_count, issues)


def split_images(session: Path, val_ratio: float = 0.2, seed: int = 17) -> dict[str, list[str]]:
    root = Path(session).expanduser().resolve()
    images = sorted(path.relative_to(root / "images").as_posix() for path in (root / "images").rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        raise ValueError("no dataset images found")
    ratio = min(max(float(val_ratio), 0.0), 0.9)
    shuffled = list(images)
    random.Random(int(seed)).shuffle(shuffled)
    val_count = max(1, round(len(shuffled) * ratio)) if len(shuffled) > 1 and ratio > 0 else 0
    return {"train": sorted(shuffled[val_count:]), "val": sorted(shuffled[:val_count])}


def write_yolo_dataset_yaml(path: Path, train: Path, val: Path) -> None:
    Path(path).write_text(
        "path: .\n"
        f"train: {Path(train).as_posix()}\n"
        f"val: {Path(val).as_posix()}\n"
        "names:\n  0: rice\n  1: flower\n",
        encoding="utf-8",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查 yubei YOLO 数据集")
    parser.add_argument("session", type=Path)
    args = parser.parse_args(argv)
    report = validate_dataset(args.session)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

