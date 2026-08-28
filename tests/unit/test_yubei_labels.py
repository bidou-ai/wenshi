import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from yubei.label_server import LabelStore


def _session(tmp_path: Path) -> Path:
    root = tmp_path / "session"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    (root / "ambiguous").mkdir()
    assert cv2.imwrite(str(root / "images" / "a.jpg"), np.zeros((1000, 1000, 3), dtype=np.uint8))
    return root


def test_label_store_exposes_capture_batch_metadata(tmp_path):
    root = _session(tmp_path)
    (root / "manifest.json").write_text(
        json.dumps({"images": [{"filename": "images/a.jpg", "capture_tag": "flower", "seq": 9}]}),
        encoding="utf-8",
    )
    item = LabelStore(root).list_images()[0]
    assert item["capture_tag"] == "flower"
    assert item["seq"] == 9


def test_label_store_preserves_nested_capture_metadata(tmp_path):
    root = _session(tmp_path)
    (root / "images" / "nested").mkdir()
    cv2.imwrite(str(root / "images" / "nested" / "a.jpg"), np.zeros((1000, 1000, 3), dtype=np.uint8))
    (root / "manifest.json").write_text(
        json.dumps({"images": [{"filename": "images/nested/a.jpg", "capture_tag": "flower"}]}),
        encoding="utf-8",
    )
    item = next(value for value in LabelStore(root).list_images() if value["name"] == "nested/a.jpg")
    assert item["capture_tag"] == "flower"


def test_label_store_lists_images_and_round_trips_boxes(tmp_path):
    store = LabelStore(_session(tmp_path))
    assert store.list_images()[0]["name"] == "a.jpg"
    store.save("a.jpg", [{"class_name": "rice", "x": 10, "y": 20, "width": 100, "height": 200}], "labelled")
    value = store.load("a.jpg")
    assert value["boxes"][0]["class_name"] == "rice"
    output = store.labels_dir / "a.txt"
    assert output.is_file()
    assert output.read_text(encoding="utf-8").strip() == "0 0.06 0.12 0.1 0.2"


def test_label_store_rejects_path_traversal_and_unknown_class(tmp_path):
    store = LabelStore(_session(tmp_path))
    with pytest.raises(ValueError, match="image"):
        store.load("../secret.jpg")
    with pytest.raises(ValueError, match="class"):
        store.save("a.jpg", [{"class_name": "weed", "x": 0, "y": 0, "width": 1, "height": 1}], "labelled")


def test_label_store_rejects_out_of_bounds_box(tmp_path):
    store = LabelStore(_session(tmp_path))
    with pytest.raises(ValueError, match="bounds"):
        store.save("a.jpg", [{"class_name": "rice", "x": -1, "y": 0, "width": 2, "height": 2}], "labelled")


def test_ambiguous_image_cannot_keep_a_trainable_yolo_label(tmp_path):
    store = LabelStore(_session(tmp_path))
    box = {"class_name": "rice", "x": 10, "y": 20, "width": 100, "height": 200}
    store.save("a.jpg", [box], "labelled")
    assert (store.labels_dir / "a.txt").is_file()

    store.save("a.jpg", [box], "ambiguous")

    assert not (store.labels_dir / "a.txt").exists()


def test_label_store_exports_yolo_without_opencv_for_windows_labeling(tmp_path, monkeypatch):
    root = tmp_path / "session"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    (root / "ambiguous").mkdir()
    (root / "images" / "sample.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x01\x90"
        b"\x00\x00\x00\xc8"
        b"\x08\x02\x00\x00\x00"
        b"\x00\x00\x00\x00"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    monkeypatch.setattr("yubei.label_server.cv2", None)
    store = LabelStore(root)

    store.save(
        "sample.png",
        [{"class_name": "flower", "x": 40, "y": 20, "width": 80, "height": 40}],
        "labelled",
    )

    assert (root / "labels" / "sample.txt").read_text(encoding="utf-8").strip() == "1 0.2 0.2 0.2 0.2"
