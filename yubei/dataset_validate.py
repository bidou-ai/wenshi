"""Offline validation and deterministic split for YOLO datasets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import random
import shutil
from typing import Any

import cv2
import yaml


@dataclass
class ValidationReport:
    ok: bool
    image_count: int
    label_count: int
    issues: list[str] = field(default_factory=list)
    class_counts: dict[str, int] = field(default_factory=lambda: {"rice": 0, "flower": 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "image_count": self.image_count,
            "label_count": self.label_count,
            "class_counts": self.class_counts,
            "issues": self.issues,
        }


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
TRAINING_STATUS = "labelled"


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


def _images(root: Path) -> list[Path]:
    images_dir = root / "images"
    if not images_dir.is_dir():
        return []
    return sorted(
        path
        for path in images_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _sidecar(root: Path, image: Path, suffix: str) -> Path:
    relative = image.relative_to(root / "images")
    return root / "labels" / relative.with_suffix(suffix)


def _metadata(root: Path, image: Path) -> dict[str, Any] | None:
    path = _sidecar(root, image, ".json")
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"label metadata is not an object: {path.name}")
    return value


def _eligible_images(root: Path) -> tuple[list[Path], int]:
    images = _images(root)
    metadata_mode = any((root / "labels").rglob("*.json"))
    if not metadata_mode:
        return images, 0
    eligible: list[Path] = []
    excluded = 0
    for image in images:
        value = _metadata(root, image)
        if value is not None and value.get("status") == TRAINING_STATUS:
            eligible.append(image)
        else:
            excluded += 1
    return eligible, excluded


def validate_dataset(session: Path) -> ValidationReport:
    root = Path(session).expanduser().resolve()
    images_dir = root / "images"
    labels_dir = root / "labels"
    issues: list[str] = []
    images = _images(root)
    if not images_dir.is_dir():
        issues.append("missing images directory")
    if not labels_dir.is_dir():
        issues.append("missing labels directory")
    if not images:
        issues.append("no dataset images found")
    label_count = 0
    class_counts = {"rice": 0, "flower": 0}
    metadata_mode = labels_dir.is_dir() and any(labels_dir.rglob("*.json"))
    for image in images:
        loaded = cv2.imread(str(image), cv2.IMREAD_UNCHANGED)
        if loaded is None:
            issues.append(f"cannot decode image: {image.name}")
        if metadata_mode:
            try:
                metadata = _metadata(root, image)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                issues.append(str(exc))
                continue
            if metadata is None:
                issues.append(f"unlabelled image: {image.name}")
                continue
            status = metadata.get("status")
            if status not in {"labelled", "ambiguous", "skipped", "unlabelled"}:
                issues.append(f"invalid label status for {image.name}: {status}")
                continue
            if status != TRAINING_STATUS:
                continue
        label_path = _sidecar(root, image, ".txt")
        if not label_path.exists():
            issues.append(f"missing label: {label_path.name}")
            continue
        label_count += 1
        for number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                parsed = _parse_line(line, label_path, number)
                if parsed is not None:
                    class_counts["rice" if parsed[0] == 0 else "flower"] += 1
            except ValueError as exc:
                issues.append(str(exc))
    if metadata_mode and images and label_count == 0:
        issues.append("no trainable labelled images found")
    return ValidationReport(not issues, len(images), label_count, issues, class_counts)


def _contains_class(root: Path, relative_name: str, class_id: int) -> bool:
    label_path = root / "labels" / Path(relative_name).with_suffix(".txt")
    try:
        lines = label_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.split() and line.split()[0] == str(class_id) for line in lines)


def _capture_value(metadata: dict[str, Any] | None, key: str) -> str:
    if not metadata:
        return ""
    value = metadata.get(key)
    if value is None and isinstance(metadata.get("capture"), dict):
        value = metadata["capture"].get(key)
    return str(value).strip() if value is not None else ""


def _split_group_key(root: Path, relative_name: str) -> str:
    metadata = _metadata(root, root / "images" / relative_name)
    plant_id = _capture_value(metadata, "plant_id")
    if plant_id:
        return f"plant:{plant_id}"
    capture_batch = _capture_value(metadata, "capture_batch")
    if capture_batch:
        return f"capture_batch:{capture_batch}"
    return f"session:{root}"


def _choose_validation_groups(
    groups: list[tuple[str, list[str]]],
    val_ratio: float,
    seed: int,
) -> set[str]:
    if len(groups) < 2 or val_ratio <= 0:
        return set()
    shuffled = list(groups)
    random.Random(int(seed)).shuffle(shuffled)
    target = sum(len(names) for _, names in shuffled) * val_ratio
    selected: list[tuple[str, list[str]]] = []
    selected_count = 0
    for group in shuffled:
        if len(selected) == len(shuffled) - 1:
            continue
        candidate_count = selected_count + len(group[1])
        if abs(candidate_count - target) < abs(selected_count - target):
            selected.append(group)
            selected_count = candidate_count
    if not selected:
        selected = [min(shuffled, key=lambda group: (abs(len(group[1]) - target), group[0]))]
    if len(selected) == len(shuffled):
        selected = selected[:-1]
    return {name for name, _ in selected}


def _stratified_split(
    root: Path,
    names: list[str],
    val_ratio: float,
    seed: int,
) -> dict[str, list[str]]:
    if not names:
        raise ValueError("no dataset images found")
    ratio = min(max(float(val_ratio), 0.0), 0.9)
    grouped: dict[str, list[str]] = {}
    for name in names:
        grouped.setdefault(_split_group_key(root, name), []).append(name)
    flower_groups: list[tuple[str, list[str]]] = []
    other_groups: list[tuple[str, list[str]]] = []
    for name, group_names in sorted(grouped.items()):
        target = flower_groups if any(_contains_class(root, item, 1) for item in group_names) else other_groups
        target.append((name, group_names))
    combined = {"train": [], "val": []}
    for offset, groups in enumerate((flower_groups, other_groups)):
        selected = _choose_validation_groups(groups, ratio, seed + offset)
        for name, group_names in groups:
            combined["val" if name in selected else "train"].extend(group_names)
    return {key: sorted(values) for key, values in combined.items()}


def split_images(session: Path, val_ratio: float = 0.2, seed: int = 17) -> dict[str, list[str]]:
    root = Path(session).expanduser().resolve()
    eligible, _ = _eligible_images(root)
    names = [path.relative_to(root / "images").as_posix() for path in eligible]
    return _stratified_split(root, names, val_ratio, seed)


def write_yolo_dataset_yaml(
    path: Path,
    train: Path,
    val: Path,
    dataset_root: Path | None = None,
) -> None:
    value = {
        "path": str(Path(dataset_root).resolve()) if dataset_root is not None else ".",
        "train": Path(train).as_posix(),
        "val": Path(val).as_posix(),
        "names": {0: "rice", 1: "flower"},
    }
    Path(path).write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def prepare_yolo_dataset(
    session: Path,
    output: Path,
    val_ratio: float = 0.2,
    seed: int = 17,
) -> dict[str, Any]:
    root = Path(session).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    report = validate_dataset(root)
    if not report.ok:
        raise ValueError("dataset validation failed: " + "; ".join(report.issues))
    eligible, excluded = _eligible_images(root)
    names = [path.relative_to(root / "images").as_posix() for path in eligible]
    split = _stratified_split(root, names, val_ratio, seed)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"prepared dataset directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for split_name, relative_names in split.items():
        for relative_name in relative_names:
            image_source = root / "images" / relative_name
            image_output = destination / split_name / "images" / relative_name
            relative_path = Path(relative_name)
            label_source = root / "labels" / relative_path.with_suffix(".txt")
            label_output = destination / split_name / "labels" / relative_path.with_suffix(".txt")
            image_output.parent.mkdir(parents=True, exist_ok=True)
            label_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image_source, image_output)
            shutil.copy2(label_source, label_output)
    write_yolo_dataset_yaml(
        destination / "data.yaml",
        Path("train/images"),
        Path("val/images"),
        dataset_root=destination,
    )
    result = {
        "source": str(root),
        "output": str(destination),
        "included_images": len(names),
        "excluded_images": excluded,
        "train_images": len(split["train"]),
        "val_images": len(split["val"]),
        "class_counts": report.class_counts,
        "seed": int(seed),
    }
    (destination / "prepare_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查 yubei YOLO 数据集")
    parser.add_argument("session", type=Path)
    parser.add_argument("--prepare", type=Path, help="验证通过后生成 train/val 数据集")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(argv)
    report = validate_dataset(args.session)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    if not report.ok:
        return 1
    if args.prepare:
        try:
            result = prepare_yolo_dataset(args.session, args.prepare, args.val_ratio, args.seed)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"prepared": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
