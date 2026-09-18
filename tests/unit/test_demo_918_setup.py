import hashlib
import json
from pathlib import Path

import numpy as np
import pytest


def _write_map(path: Path, *, name: str = "new-site") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "header": {
            "mapType": "2D-Map",
            "mapName": name,
            "minPos": {"x": -2.0, "y": -2.0},
            "maxPos": {"x": 3.0, "y": 2.0},
            "resolution": 0.05,
            "version": "1.0.6",
        },
        "normalPosList": [],
        "advancedPointList": [],
        "advancedCurveList": [],
        "advancedLineList": [],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def complete_918_setup(tmp_path: Path, *, mode: str = "shuttle", observation_count: int = 1):
    from wenshi_patrol.demo_918_setup import Setup918Session

    runtime_root = tmp_path / "runtime" / "9.18"
    session = Setup918Session(runtime_root / "setup_20260918_150000", runtime_root)
    session.set_operator("cjc")
    session.import_map(_write_map(tmp_path / "incoming" / "site.smap"))
    image = np.full((12, 16, 3), 127, dtype=np.uint8)
    session.set_route(mode, 2)
    session.record_anchor(
        "R-01", {"x": 0.0, "y": 0.0, "angle": 0.0}, photo=image, source="agv_status"
    )
    session.record_anchor(
        "R-02", {"x": 2.0, "y": 0.0, "angle": 0.0}, photo=image, source="agv_status"
    )
    session.set_observation_count(observation_count)
    for index in range(1, observation_count + 1):
        x = 2.0 * index / (observation_count + 1)
        session.record_observation(
            f"P-{index:02d}",
            {"x": x, "y": 0.05, "angle": 0.0},
            arm_view="left" if index % 2 else "right",
            photo=image,
            note=f"plant {index}",
        )
    for index, name in enumerate(("home_safe", "left", "right")):
        session.record_viewpoint(name, [float(index)] * 6)
    return session


def test_918_setup_publishes_dynamic_no_tag_schema(tmp_path):
    from wenshi_patrol.demo_918_setup import load_918_setup

    setup = complete_918_setup(tmp_path, mode="shuttle", observation_count=1)
    destination = tmp_path / "runtime" / "9.18" / "demo_setup.json"

    setup.publish(destination)

    value = json.loads(destination.read_text(encoding="utf-8"))
    assert value["kind"] == "wenshi_918_expert_demo"
    assert value["route"]["mode"] == "shuttle"
    assert set(value["observations"]) == {"P-01"}
    assert "tags" not in value
    assert "calibration_board" not in value
    loaded = load_918_setup(destination)
    assert loaded["route_order"] == ["R-01", "R-02"]
    assert loaded["observations"]["P-01"]["arm_view"] == "left"


@pytest.mark.parametrize("count", (1, 2, 3, 5))
def test_918_setup_accepts_dynamic_observation_counts(tmp_path, count):
    from wenshi_patrol.demo_918_setup import load_918_setup

    destination = tmp_path / "runtime" / "9.18" / "demo_setup.json"
    complete_918_setup(tmp_path, observation_count=count).publish(destination)

    assert len(load_918_setup(destination)["observations"]) == count


def test_918_setup_map_snapshot_is_immutable_and_hash_checked(tmp_path):
    from wenshi_patrol.demo_918_setup import load_918_setup

    destination = tmp_path / "runtime" / "9.18" / "demo_setup.json"
    complete_918_setup(tmp_path).publish(destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    map_path = (destination.parent / payload["map"]["path"]).resolve()
    assert map_path.name.startswith("map-") and map_path.suffix == ".smap"
    assert hashlib.sha256(map_path.read_bytes()).hexdigest() == payload["map"]["sha256"]

    map_path.write_text(map_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256|指纹"):
        load_918_setup(destination)


def test_918_setup_rejects_observation_away_from_route(tmp_path):
    setup = complete_918_setup(tmp_path)
    setup.observations["P-01"]["pose"] = {"x": 1.0, "y": 0.50, "angle": 0.0}

    with pytest.raises(ValueError, match="偏离.*路线"):
        setup.publish(tmp_path / "runtime" / "9.18" / "demo_setup.json")


def test_918_setup_rejects_adjacent_route_anchors_that_are_too_close(tmp_path):
    setup = complete_918_setup(tmp_path)
    setup.anchors["R-02"]["pose"] = {"x": 0.10, "y": 0.0, "angle": 0.0}

    with pytest.raises(ValueError, match="距离.*过短"):
        setup.publish(tmp_path / "runtime" / "9.18" / "demo_setup.json")


def test_918_setup_loader_rejects_photo_outside_918_runtime(tmp_path):
    from wenshi_patrol.demo_918_setup import load_918_setup

    destination = tmp_path / "runtime" / "9.18" / "demo_setup.json"
    complete_918_setup(tmp_path).publish(destination)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not used")
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["observations"]["P-01"]["photo"] = "../../outside.jpg"
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="不在.*9.18|目录"):
        load_918_setup(destination)


def test_918_setup_resume_restores_dynamic_progress(tmp_path):
    from wenshi_patrol.demo_918_setup import Setup918Session

    setup = complete_918_setup(tmp_path, mode="loop", observation_count=2)
    resumed = Setup918Session.resume(setup.root, setup.runtime_root)

    assert resumed.operator == "cjc"
    assert resumed.route_mode == "loop"
    assert resumed.anchor_count == 2
    assert resumed.observation_count == 2
    assert set(resumed.observations) == {"P-01", "P-02"}
    assert set(resumed.viewpoints) == {"home_safe", "left", "right"}


def test_918_setup_copies_arm_poses_once_without_runtime_dependency(tmp_path):
    from wenshi_patrol.demo_918_setup import Setup918Session

    runtime_root = tmp_path / "runtime" / "9.18"
    source = tmp_path / "runtime" / "demo" / "demo_setup.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({
            "viewpoints": {
                "home_safe": {"joint": [0.0] * 6},
                "left": {"joint": [1.0] * 6},
                "right": {"joint": [2.0] * 6},
            }
        }),
        encoding="utf-8",
    )
    setup = Setup918Session(runtime_root / "setup_copy", runtime_root)

    setup.copy_viewpoints(source)
    source.unlink()

    assert setup.viewpoints["left"]["joint"] == [1.0] * 6
    assert "copied_from_sha256" in setup.viewpoints["left"]
    resumed = Setup918Session.resume(setup.root, runtime_root)
    assert resumed.viewpoints["right"]["joint"] == [2.0] * 6


def test_918_setup_rejects_invalid_arm_view(tmp_path):
    setup = complete_918_setup(tmp_path)
    setup.observations["P-01"]["arm_view"] = "center"

    with pytest.raises(ValueError, match="left|right"):
        setup.publish(tmp_path / "runtime" / "9.18" / "demo_setup.json")
