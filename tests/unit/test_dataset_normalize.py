import json
from pathlib import Path

import cv2
import numpy as np

from yubei.dataset_normalize import normalize_session


def test_normalize_session_rotates_images_and_preserves_raw_session(tmp_path: Path):
    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    (source / "labels").mkdir()
    (source / "ambiguous").mkdir()
    image = np.zeros((4, 8, 3), dtype=np.uint8)
    image[0, 0] = (1, 2, 3)
    cv2.imwrite(str(source / "images" / "000001.jpg"), image)
    (source / "manifest.json").write_text(json.dumps({"classes": {"rice_plant": 0}, "images": []}), encoding="utf-8")

    output = normalize_session(source, tmp_path / "normalized")

    rotated = cv2.imread(str(output / "images" / "000001.jpg"))
    assert rotated.shape[:2] == (8, 4)
    assert (source / "images" / "000001.jpg").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["normalization"]["rotation"] == "clockwise_90"


def test_normalize_session_can_preserve_panicle_orientation(tmp_path: Path):
    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    (source / "labels").mkdir()
    (source / "ambiguous").mkdir()
    cv2.imwrite(str(source / "images" / "000001.jpg"), np.zeros((4, 8, 3), dtype=np.uint8))

    output = normalize_session(source, tmp_path / "normalized", rotation="none")

    preserved = cv2.imread(str(output / "images" / "000001.jpg"))
    assert preserved.shape[:2] == (4, 8)
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8"))["normalization"]["rotation"] == "none"
