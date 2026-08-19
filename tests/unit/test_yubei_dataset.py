import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from yubei.dataset_validate import split_images, validate_dataset, write_yolo_dataset_yaml
from yubei.publish_model import publish_model


def _dataset(tmp_path: Path, count: int = 3) -> Path:
    root = tmp_path / "session"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    (root / "ambiguous").mkdir()
    for index in range(count):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        cv2.imwrite(str(root / "images" / f"{index:02d}.jpg"), image)
        (root / "labels" / f"{index:02d}.txt").write_text("0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
    return root


def test_validate_dataset_accepts_valid_yolo_files(tmp_path):
    report = validate_dataset(_dataset(tmp_path))
    assert report.ok is True
    assert report.image_count == 3
    assert report.label_count == 3


def test_validate_dataset_rejects_out_of_range_box(tmp_path):
    root = _dataset(tmp_path, 1)
    (root / "labels" / "00.txt").write_text("0 1.2 0.5 0.5 0.5\n", encoding="utf-8")
    report = validate_dataset(root)
    assert report.ok is False
    assert any("range" in issue for issue in report.issues)


def test_split_is_deterministic_and_disjoint(tmp_path):
    root = _dataset(tmp_path, 10)
    first = split_images(root, val_ratio=0.2, seed=17)
    second = split_images(root, val_ratio=0.2, seed=17)
    assert first == second
    assert set(first["train"]).isdisjoint(first["val"])
    assert len(first["val"]) == 2


def test_write_yaml_contains_classes(tmp_path):
    path = tmp_path / "data.yaml"
    write_yolo_dataset_yaml(path, Path("train"), Path("val"))
    text = path.read_text(encoding="utf-8")
    assert "rice" in text and "flower" in text


def test_publish_model_archives_existing_and_writes_sha(tmp_path):
    source = tmp_path / "best.pt"
    source.write_bytes(b"model-v2")
    formal = tmp_path / "models"
    formal.mkdir()
    (formal / "rice_demo.pt").write_bytes(b"model-v1")
    output = publish_model(source, formal, {"source_run": "test"})
    assert output == formal / "rice_demo.pt"
    assert output.read_bytes() == b"model-v2"
    metadata = json.loads((formal / "rice_demo.json").read_text(encoding="utf-8"))
    assert metadata["source_run"] == "test"
    assert metadata["sha256"]
    assert list((formal / "archive").glob("rice_demo.pt.*"))


def test_publish_refuses_non_pt(tmp_path):
    with pytest.raises(ValueError, match="\.pt"):
        publish_model(tmp_path / "best.onnx", tmp_path / "models", {})
