import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from wenshi_patrol.vision.run_store import PatrolRunStore


def test_target_store_keeps_only_far_near_and_metadata(tmp_path: Path):
    store = PatrolRunStore.create(tmp_path / "runs")
    target = store.create_target()
    target.save_far(np.zeros((4, 5, 3), dtype=np.uint8), {"side": "left"})
    target.save_near(np.ones((4, 5, 3), dtype=np.uint8), {"quality": 0.8})
    assert sorted(path.name for path in target.path.iterdir()) == ["far.jpg", "metadata.json", "near.jpg"]
    assert cv2.imread(str(target.path / "far.jpg")).shape == (4, 5, 3)


def test_store_rejects_non_run_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="run_"):
        PatrolRunStore(tmp_path / "ad_hoc")


def test_event_and_metadata_are_json_lines(tmp_path: Path):
    store = PatrolRunStore.create(tmp_path / "runs")
    target = store.create_target()
    target.write_metadata({"route_segment": "LM1->LM4"})
    store.append_event("target_created", target_id=target.target_id)
    metadata = json.loads((target.path / "metadata.json").read_text(encoding="utf-8"))
    event = json.loads((store.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert metadata["route_segment"] == "LM1->LM4"
    assert event["event"] == "target_created"


def test_create_target_numbers_targets(tmp_path: Path):
    store = PatrolRunStore.create(tmp_path / "runs")
    assert store.create_target().target_id == "T0001"
    assert store.create_target().target_id == "T0002"


def test_run_status_can_finish_and_reopen(tmp_path: Path):
    store = PatrolRunStore.create(tmp_path / "runs")
    assert json.loads(store.run_path.read_text(encoding="utf-8"))["status"] == "running"
    store.finish("stopped")
    assert json.loads(store.run_path.read_text(encoding="utf-8"))["status"] == "stopped"
    store.reopen()
    value = json.loads(store.run_path.read_text(encoding="utf-8"))
    assert value["status"] == "running"
    assert "finished_at" not in value
