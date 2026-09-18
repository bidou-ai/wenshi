import json

import cv2
import numpy as np
import pytest


def _complete_session(tmp_path):
    from wenshi_patrol.demo_setup import DEMO_PLANTS, RECORDED_STATIONS, DemoSetupSession

    session = DemoSetupSession(tmp_path / "runtime" / "demo" / "setup_evidence")
    session.operator = "field-operator"
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    session.record_calibration_board(image)
    for plant_id, tag_id, observed in DEMO_PLANTS:
        session.record_tag(plant_id, tag_id, photo=image, observed=observed)
    station_poses = [
        *[(float(-0.9 + index * 0.5), -2.20, 3.14159) for index in range(8)],
        *[(float(2.6 - index * 0.5), 0.12, 0.0) for index in range(8)],
    ]
    for group_id, (x, y, angle) in zip(RECORDED_STATIONS, station_poses):
        session.record_station(
            group_id,
            {"x": x, "y": y, "angle": angle},
            photo=image,
            source="agv_status",
        )
    session.migrate_and_mirror_stations(top_route_y=0.097, bottom_route_y=-2.334)
    for index, name in enumerate(("home_safe", "left", "right")):
        session.record_viewpoint(name, [float(index)] * 6)
    return session


def test_demo_setup_publishes_only_to_demo_schema_and_keeps_all_tag_evidence(tmp_path):
    session = _complete_session(tmp_path)
    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"

    session.publish(destination)

    value = json.loads(destination.read_text(encoding="utf-8"))
    assert value["kind"] == "wenshi_expert_demo"
    assert len(value["tags"]) == 32
    assert len(value["stations"]) == 24
    assert value["calibration_board"]["tag_id"] == 0
    assert value["tags"]["A-01"]["tag_id"] == 1
    assert value["tags"]["C-01"]["tag_id"] == 32
    assert value["tags"]["C-01"]["observed"] is False
    assert set(value["viewpoints"]) == {"home_safe", "left", "right"}


def test_demo_setup_can_publish_with_deferred_c_tags(tmp_path):
    from wenshi_patrol.demo_setup import DEMO_PLANTS, load_demo_setup

    session = _complete_session(tmp_path)
    for plant_id, _tag_id, observed in DEMO_PLANTS:
        if not observed:
            session.tags.pop(plant_id)
    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"

    session.publish(destination)

    value = json.loads(destination.read_text(encoding="utf-8"))
    assert len(value["tags"]) == 24
    assert "B-R-08" in value["tags"]
    assert "C-01" not in value["tags"]
    assert load_demo_setup(destination)["raw"]["tags"] == value["tags"]


def test_demo_setup_ignores_legacy_center_viewpoint_when_publishing(tmp_path):
    session = _complete_session(tmp_path)
    session.viewpoints["center"] = {"joint": [9.0] * 6, "recorded_at": "legacy"}
    session.checkpoint()

    resumed = session.resume(session.root)
    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    resumed.publish(destination)

    value = json.loads(destination.read_text(encoding="utf-8"))
    assert set(value["viewpoints"]) == {"home_safe", "left", "right"}


def test_demo_setup_rejects_formal_height_setup_even_if_stations_are_present(tmp_path):
    from wenshi_patrol.demo_setup import load_demo_setup

    path = tmp_path / "field_height_setup.json"
    path.write_text(json.dumps({"stations": {}, "viewpoints": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="独立|Demo"):
        load_demo_setup(path)


def test_demo_setup_requires_agv_realtime_station_poses(tmp_path):
    session = _complete_session(tmp_path)
    session.stations["A-01"]["pose_source"] = "manual"

    with pytest.raises(ValueError, match="AGV"):
        session.publish(tmp_path / "runtime" / "demo" / "demo_setup.json")


def test_demo_setup_loader_requires_published_schema_and_existing_photos(tmp_path):
    from wenshi_patrol.demo_setup import load_demo_setup

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    _complete_session(tmp_path).publish(destination)
    value = json.loads(destination.read_text(encoding="utf-8"))
    value["tags"]["A-01"].pop("photo")
    destination.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="照片"):
        load_demo_setup(destination)


def test_demo_setup_loader_reports_malformed_station_as_value_error(tmp_path):
    from wenshi_patrol.demo_setup import load_demo_setup

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    _complete_session(tmp_path).publish(destination)
    value = json.loads(destination.read_text(encoding="utf-8"))
    value["stations"]["A-01"] = None
    destination.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="A-01"):
        load_demo_setup(destination)


def test_demo_setup_resumes_legacy_photos_at_tag_19_and_creates_draft(tmp_path):
    from wenshi_patrol.demo_setup import DEMO_PLANTS, DemoSetupSession

    root = tmp_path / "runtime" / "demo" / "setup_20260918_105456"
    image = np.full((24, 32, 3), 127, dtype=np.uint8)
    (root / "reference").mkdir(parents=True)
    (root / "tags").mkdir()
    assert cv2.imwrite(str(root / "reference" / "tag-0-calibration-board.jpg"), image)
    for plant_id, _tag_id, _observed in DEMO_PLANTS[:18]:
        assert cv2.imwrite(str(root / "tags" / f"{plant_id}.jpg"), image)

    resumed = DemoSetupSession.resume(root)

    assert resumed.calibration_board["photo"] == "reference/tag-0-calibration-board.jpg"
    assert len(resumed.tags) == 18
    assert resumed.next_missing_tag() == ("B-R-03", 19, True)
    assert (root / "setup_draft.json").is_file()


def test_demo_setup_checkpoint_restores_station_and_viewpoint(tmp_path):
    from wenshi_patrol.demo_setup import DemoSetupSession

    root = tmp_path / "runtime" / "demo" / "setup_20260918_110000"
    image = np.full((24, 32, 3), 127, dtype=np.uint8)
    setup = DemoSetupSession(root)
    setup.set_operator("cjc")
    setup.record_calibration_board(image)
    setup.record_station(
        "left-01",
        {"x": 1.25, "y": -0.5, "angle": 0.75},
        photo=image,
        source="agv_status",
        note="first plant",
    )
    setup.record_viewpoint("home_safe", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    resumed = DemoSetupSession.resume(root)

    assert resumed.operator == "cjc"
    assert resumed.stations["left-01"]["pose"] == {"x": 1.25, "y": -0.5, "angle": 0.75}
    assert resumed.stations["left-01"]["note"] == "first plant"
    assert resumed.viewpoints["home_safe"]["joint"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_demo_setup_resume_rejects_corrupt_legacy_photo(tmp_path):
    from wenshi_patrol.demo_setup import DemoSetupSession

    root = tmp_path / "runtime" / "demo" / "setup_20260918_110000"
    (root / "tags").mkdir(parents=True)
    (root / "tags" / "A-01.jpg").write_text("not a jpeg", encoding="utf-8")

    with pytest.raises(ValueError, match="无法读取.*A-01"):
        DemoSetupSession.resume(root)


def test_demo_setup_migrates_recorded_a_and_b_right_and_derives_b_left(tmp_path):
    from wenshi_patrol.demo_setup import DemoSetupSession

    image = np.full((24, 32, 3), 127, dtype=np.uint8)
    setup = DemoSetupSession(tmp_path / "runtime" / "demo" / "setup_evidence")
    for index in range(1, 9):
        setup.record_station(
            f"left-{index:02d}",
            {"x": float(index), "y": -2.20, "angle": 3.12},
            photo=image,
            source="agv_status",
        )
        setup.record_station(
            f"right-{index:02d}",
            {"x": float(index) + 0.25, "y": 0.12, "angle": 0.01},
            photo=image,
            source="agv_status",
        )

    setup.migrate_and_mirror_stations(top_route_y=0.097, bottom_route_y=-2.334)

    assert len(setup.stations) == 24
    assert setup.stations["A-01"]["pose"] == {"x": 1.0, "y": -2.2, "angle": 3.12}
    assert setup.stations["B-R-01"]["pose"] == {"x": 1.25, "y": 0.12, "angle": 0.01}
    mirrored = setup.stations["B-L-01"]
    assert mirrored["pose_source"] == "map_mirror"
    assert mirrored["derived_from"] == "B-R-01"
    assert mirrored["pose"]["x"] == pytest.approx(1.25)
    assert mirrored["pose"]["y"] == pytest.approx(-2.357)
    assert abs(abs(mirrored["pose"]["angle"]) - np.pi) < 0.02


def test_demo_setup_rejects_tampered_b_left_mirror(tmp_path):
    session = _complete_session(tmp_path)
    session.stations["B-L-01"]["pose"]["x"] += 0.2

    with pytest.raises(ValueError, match="B-L-01.*镜像"):
        session.publish(tmp_path / "runtime" / "demo" / "demo_setup.json")
