import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from wenshi_patrol.phenotyping.capture_schema import atomic_json_write
from wenshi_patrol.phenotyping.plant_store import create_phenotyping_run


def test_new_run_has_phenotyping_layout_and_snapshot(tmp_path: Path):
    store = create_phenotyping_run(tmp_path / "runs", {"version": 1, "enabled": False})

    assert store.run_dir.name.startswith("run_")
    run = json.loads((store.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["config_snapshot"] == {"version": 1, "enabled": False}
    assert (store.run_dir / "events.jsonl").is_file()
    assert (store.run_dir / "agv.csv").read_text(encoding="utf-8").startswith("time,")
    assert (store.run_dir / "jaka.csv").read_text(encoding="utf-8").startswith("time,")
    assert (store.run_dir / "plants").is_dir()


def test_capture_writes_rgbd_and_traceable_frame_metadata(tmp_path: Path):
    store = create_phenotyping_run(tmp_path / "runs", {})
    plant = store.plant("A-01")
    color = np.full((8, 10, 3), 120, dtype=np.uint8)
    depth = np.full((8, 10), 900, dtype=np.uint16)

    output = plant.save_capture("left", color, depth, {
        "frame_seq": 7,
        "timestamp": "2026-08-28T01:02:03Z",
        "camera_intrinsics": {"fx": 1.0},
        "tag_id": 12,
        "agv_pose": {"x": 1.2},
        "jaka_pose": {"tcp": [0, 1, 2]},
        "quality": {"status": "ok"},
        "retry": 1,
    })

    assert output == plant.path / "captures" / "left" / "color.jpg"
    assert cv2.imread(str(output)).shape == (8, 10, 3)
    assert cv2.imread(str(output.parent / "depth.png"), cv2.IMREAD_UNCHANGED).dtype == np.uint16
    frame = json.loads((output.parent / "frame.json").read_text(encoding="utf-8"))
    assert frame["view"] == "left"
    assert frame["color"]["file"] == "color.jpg"
    assert frame["depth"]["shape"] == [8, 10]
    assert frame["tag_id"] == 12


def test_capture_without_depth_records_missing_depth(tmp_path: Path):
    plant = create_phenotyping_run(tmp_path / "runs", {}).plant("A-01")
    plant.save_capture("center", np.zeros((3, 4, 3), dtype=np.uint8), None, {})
    frame = json.loads((plant.path / "captures/center/frame.json").read_text(encoding="utf-8"))
    assert frame["depth"] is None
    assert not (plant.path / "captures/center/depth.png").exists()


def test_capture_rejects_non_json_metadata_before_replacing_existing_images(tmp_path: Path):
    plant = create_phenotyping_run(tmp_path / "runs", {}).plant("A-01")
    first = np.full((4, 4, 3), 32, dtype=np.uint8)
    plant.save_capture("left", first, None, {"quality": {"score": 0.8}})
    original = (plant.path / "captures/left/color.jpg").read_bytes()

    with pytest.raises(ValueError, match="JSON"):
        plant.save_capture("left", np.full((4, 4, 3), 224, dtype=np.uint8), None, {"quality": {"score": float("nan")}})

    assert (plant.path / "captures/left/color.jpg").read_bytes() == original


def test_traits_review_and_resume_preserve_existing_data(tmp_path: Path):
    store = create_phenotyping_run(tmp_path / "runs", {"batch": "one"})
    plant = store.plant("A-01")
    plant.write_trait("plant_height", {"auto_value": 0.8, "reviewed_value": None})
    plant.write_trait("plant_height", {"reviewed_value": 0.82, "reasons": ["operator_adjusted"]})
    plant.update_review({"state": "needs_review", "reviewed_by": "operator"})
    store.append_event("capture_saved", plant_id="A-01")
    resumed = type(store)(store.run_dir)

    assert json.loads((resumed.run_dir / "run.json").read_text(encoding="utf-8"))["config_snapshot"] == {"batch": "one"}
    trait = resumed.plant("A-01").trait("plant_height")
    assert trait["auto_value"] == 0.8
    assert trait["reviewed_value"] == 0.82
    assert trait["reasons"] == ["operator_adjusted"]
    assert json.loads((resumed.run_dir / "plants/A-01/review.json").read_text(encoding="utf-8"))["state"] == "needs_review"
    assert len((resumed.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_atomic_json_replaces_complete_document(tmp_path: Path):
    path = tmp_path / "nested" / "value.json"
    atomic_json_write(path, {"version": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 2}
    assert not list(path.parent.glob(".*.tmp-*"))


@pytest.mark.parametrize("bad", ["../A-01", "A/01", "/tmp/A-01", "..", ""])
def test_rejects_path_traversal_and_invalid_plant_ids(tmp_path: Path, bad: str):
    store = create_phenotyping_run(tmp_path / "runs", {})
    with pytest.raises(ValueError):
        store.plant(bad)


@pytest.mark.parametrize("bad", ["middle", "../left", "", "left/right"])
def test_rejects_invalid_views(tmp_path: Path, bad: str):
    plant = create_phenotyping_run(tmp_path / "runs", {}).plant("A-01")
    with pytest.raises(ValueError):
        plant.save_capture(bad, np.zeros((2, 2, 3), dtype=np.uint8), None, {})
