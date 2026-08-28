import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import yubei.dataset_validate as dataset_validate
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


def _label_metadata(root: Path, index: int, **capture: str) -> None:
    """Write the same capture metadata shape emitted by the label server."""
    name = f"{index:02d}.jpg"
    (root / "labels" / f"{index:02d}.json").write_text(
        json.dumps({"image": name, "status": "labelled", "capture": capture}),
        encoding="utf-8",
    )


def test_validate_dataset_accepts_valid_yolo_files(tmp_path):
    report = validate_dataset(_dataset(tmp_path))
    assert report.ok is True
    assert report.image_count == 3
    assert report.label_count == 3
    assert report.class_counts == {"rice": 3, "flower": 0}


def test_validate_dataset_counts_flower_boxes(tmp_path):
    root = _dataset(tmp_path, 1)
    (root / "labels" / "00.txt").write_text(
        "0 0.5 0.5 0.5 0.5\n1 0.4 0.4 0.1 0.1\n",
        encoding="utf-8",
    )

    report = validate_dataset(root)

    assert report.class_counts == {"rice": 1, "flower": 1}


def test_validate_dataset_rejects_out_of_range_box(tmp_path):
    root = _dataset(tmp_path, 1)
    (root / "labels" / "00.txt").write_text("0 1.2 0.5 0.5 0.5\n", encoding="utf-8")
    report = validate_dataset(root)
    assert report.ok is False
    assert any("range" in issue for issue in report.issues)


def test_validate_dataset_rejects_empty_or_all_excluded_session(tmp_path):
    empty = _dataset(tmp_path / "empty", 0)
    assert validate_dataset(empty).ok is False

    excluded = _dataset(tmp_path / "excluded", 1)
    (excluded / "labels" / "00.json").write_text(
        json.dumps({"image": "00.jpg", "status": "ambiguous", "boxes": []}),
        encoding="utf-8",
    )
    report = validate_dataset(excluded)
    assert report.ok is False
    assert any("trainable" in issue for issue in report.issues)


def test_split_is_deterministic_and_disjoint(tmp_path):
    root = _dataset(tmp_path, 10)
    for index in range(10):
        _label_metadata(root, index, capture_batch=f"batch-{index}")
    first = split_images(root, val_ratio=0.2, seed=17)
    second = split_images(root, val_ratio=0.2, seed=17)
    assert first == second
    assert set(first["train"]).isdisjoint(first["val"])
    assert len(first["val"]) == 2


def test_split_keeps_all_images_of_a_plant_in_one_partition(tmp_path):
    root = _dataset(tmp_path, 6)
    for index, plant_id in enumerate(("A-01", "A-01", "A-02", "A-02", "A-03", "A-03")):
        _label_metadata(root, index, plant_id=plant_id)

    split = split_images(root, val_ratio=0.34, seed=17)
    train = set(split["train"])
    val = set(split["val"])

    assert train.isdisjoint(val)
    for first, second in (("00.jpg", "01.jpg"), ("02.jpg", "03.jpg"), ("04.jpg", "05.jpg")):
        assert (first in train) == (second in train)
        assert (first in val) == (second in val)


def test_split_uses_capture_batch_when_plant_id_is_absent(tmp_path):
    root = _dataset(tmp_path, 6)
    for index, capture_batch in enumerate(("batch-a", "batch-a", "batch-b", "batch-b", "batch-c", "batch-c")):
        _label_metadata(root, index, capture_batch=capture_batch)

    split = split_images(root, val_ratio=0.34, seed=17)
    train = set(split["train"])
    val = set(split["val"])

    for first, second in (("00.jpg", "01.jpg"), ("02.jpg", "03.jpg"), ("04.jpg", "05.jpg")):
        assert (first in train) == (second in train)
        assert (first in val) == (second in val)


def test_split_keeps_unidentified_session_in_one_partition(tmp_path):
    root = _dataset(tmp_path, 4)
    for index in range(4):
        _label_metadata(root, index)

    split = split_images(root, val_ratio=0.25, seed=17)

    assert split == {"train": ["00.jpg", "01.jpg", "02.jpg", "03.jpg"], "val": []}


def test_write_yaml_contains_classes(tmp_path):
    path = tmp_path / "data.yaml"
    write_yolo_dataset_yaml(path, Path("train"), Path("val"))
    text = path.read_text(encoding="utf-8")
    assert "rice" in text and "flower" in text


def test_prepare_creates_train_val_tree_and_excludes_ambiguous_images(tmp_path):
    root = _dataset(tmp_path, 4)
    for index in range(4):
        status = "ambiguous" if index == 3 else "labelled"
        (root / "labels" / f"{index:02d}.json").write_text(
            json.dumps({"image": f"{index:02d}.jpg", "status": status, "boxes": []}),
            encoding="utf-8",
        )
    output = tmp_path / "prepared"

    result = dataset_validate.prepare_yolo_dataset(root, output, val_ratio=0.34, seed=17)

    prepared_images = list((output / "train" / "images").glob("*.jpg")) + list(
        (output / "val" / "images").glob("*.jpg")
    )
    assert result["included_images"] == 3
    assert result["excluded_images"] == 1
    assert len(prepared_images) == 3
    assert not any(path.name == "03.jpg" for path in prepared_images)
    assert (output / "data.yaml").is_file()
    assert str(output.resolve()) in (output / "data.yaml").read_text(encoding="utf-8")


def test_prepare_stratifies_flower_images_between_train_and_val(tmp_path):
    root = _dataset(tmp_path, 6)
    for index in range(6):
        _label_metadata(root, index, plant_id=f"A-{index:02d}")
    for index in (0, 1):
        (root / "labels" / f"{index:02d}.txt").write_text(
            "0 0.5 0.5 0.5 0.5\n1 0.4 0.4 0.1 0.1\n",
            encoding="utf-8",
        )
    output = tmp_path / "prepared"

    result = dataset_validate.prepare_yolo_dataset(root, output, val_ratio=0.25, seed=4)

    train_has_flower = any(
        line.startswith("1 ")
        for path in (output / "train" / "labels").rglob("*.txt")
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    val_has_flower = any(
        line.startswith("1 ")
        for path in (output / "val" / "labels").rglob("*.txt")
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    assert train_has_flower is True
    assert val_has_flower is True
    assert result["class_counts"]["flower"] == 2


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
    with pytest.raises(ValueError, match=r"\.pt"):
        publish_model(tmp_path / "best.onnx", tmp_path / "models", {})
