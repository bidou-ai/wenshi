import json
from pathlib import Path

import pytest

from yubei.label_server import LabelStore


def _session(tmp_path: Path) -> Path:
    root = tmp_path / "session"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    (root / "ambiguous").mkdir()
    (root / "images" / "a.jpg").write_bytes(b"not-an-image-but-a-fixture")
    return root


def test_label_store_lists_images_and_round_trips_boxes(tmp_path):
    store = LabelStore(_session(tmp_path))
    assert store.list_images()[0]["name"] == "a.jpg"
    store.save("a.jpg", [{"class_name": "rice", "x": 10, "y": 20, "width": 100, "height": 200}], "labelled")
    value = store.load("a.jpg")
    assert value["boxes"][0]["class_name"] == "rice"
    output = store.export_yolo("a.jpg", image_width=1000, image_height=1000)
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

