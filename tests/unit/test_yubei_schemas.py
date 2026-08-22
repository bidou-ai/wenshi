from pathlib import Path

import pytest

from yubei.paths import load_json
from yubei.schemas import DatasetManifest


def test_manifest_records_captured_image(tmp_path: Path):
    manifest = DatasetManifest()
    manifest.add_image(
        "images/000001.jpg",
        1280,
        720,
        "captured",
        metadata={"capture_tag": "flower", "seq": 12, "quality": {"ok": True}},
    )
    path = tmp_path / "manifest.json"
    manifest.write(path)
    value = load_json(path)
    assert value["classes"] == {"rice": 0, "flower": 1}
    assert value["images"][0]["status"] == "captured"
    assert value["images"][0]["width"] == 1280
    assert value["images"][0]["capture_tag"] == "flower"
    assert value["images"][0]["seq"] == 12


def test_manifest_rejects_unknown_status():
    manifest = DatasetManifest()
    with pytest.raises(ValueError, match="status"):
        manifest.add_image("a.jpg", 1280, 720, "unknown")


def test_manifest_rejects_invalid_dimensions():
    manifest = DatasetManifest()
    with pytest.raises(ValueError, match="dimensions"):
        manifest.add_image("a.jpg", 0, 720, "captured")


def test_manifest_rejects_unknown_capture_tag():
    manifest = DatasetManifest()
    with pytest.raises(ValueError, match="capture tag"):
        manifest.add_image("a.jpg", 1280, 720, "captured", metadata={"capture_tag": "weed"})
