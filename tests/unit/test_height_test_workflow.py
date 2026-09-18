from pathlib import Path
import json

import numpy as np
import pytest


def _config():
    import yaml
    return yaml.safe_load(Path("config/wenshi.yaml").read_text(encoding="utf-8"))


def test_height_config_has_24_active_and_c_excluded():
    from wenshi_patrol.height_test.models import HeightTestConfig
    value = HeightTestConfig.from_project(_config())
    assert len(value.active_plant_ids) == 24
    assert value.group_plant_ids("right-01") == ("B-R-01",)
    assert "C-01" in value.excluded_plant_ids


def test_field_tag_mapping_matches_installed_layout():
    from wenshi_patrol.height_test.models import HeightTestConfig

    value = HeightTestConfig.from_project(_config())
    mapping = {plant.plant_id: plant.tag_id for plant in value.plants}
    assert [mapping[f"A-{index:02d}"] for index in range(1, 9)] == list(range(1, 9))
    assert [mapping[f"B-L-{index:02d}"] for index in range(1, 9)] == list(range(16, 8, -1))
    assert [mapping[f"B-R-{index:02d}"] for index in range(1, 9)] == list(range(17, 25))
    assert [mapping[f"C-{index:02d}"] for index in range(1, 9)] == list(range(32, 24, -1))
    assert set(mapping.values()) == set(range(1, 33))


def test_rgbd_packet_rejects_mismatched_dimensions():
    from wenshi_patrol.height_test.models import FramePacket
    with pytest.raises(ValueError, match="dimensions"):
        FramePacket(np.zeros((4, 4, 3), np.uint8), np.zeros((3, 4), np.uint16), 1, {"fx": 1, "fy": 1})


def test_setup_rejects_duplicate_tag_ids(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession
    session = SetupSession.begin(tmp_path, HeightTestConfig.from_project(_config()))
    session.record_tag("A-01", TagObservation(1))
    with pytest.raises(ValueError, match="duplicate"):
        session.record_tag("A-02", TagObservation(1))


def test_setup_rejects_calibration_tag_zero_for_a_plant(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession

    session = SetupSession.begin(tmp_path, HeightTestConfig.from_project(_config()))
    with pytest.raises(ValueError, match="0"):
        session.record_tag("A-01", TagObservation(0))


def test_setup_rejects_tag_that_disagrees_with_fixed_mapping(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession

    session = SetupSession.begin(tmp_path, HeightTestConfig.from_project(_config()))
    with pytest.raises(ValueError, match="A-01.*1"):
        session.record_tag("A-01", TagObservation(2))


def test_setup_publishes_c_records_and_active_station_coverage(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession
    config = HeightTestConfig.from_project(_config())
    session = SetupSession.begin(tmp_path, config)
    for plant in config.plants:
        session.record_tag(plant.plant_id, TagObservation(plant.tag_id))
        if plant.plant_id in config.active_plant_ids:
            session.record_water_offset(plant.plant_id, 0.12)
    for group_id in config.groups:
        session.record_station(group_id, {"x": 1.0, "y": 2.0, "angle": 0.0}, source="agv_status")
    destination = tmp_path / "field_height_setup.json"
    session.publish(destination)
    value = json.loads(destination.read_text(encoding="utf-8"))
    assert len(value["active_plants"]) == 24
    assert value["plants"]["C-01"]["excluded_from_detection"] is True
    assert len(value["stations"]) == 16


def test_setup_publish_requires_tags_for_excluded_c_row_too(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession

    config = HeightTestConfig.from_project(_config())
    session = SetupSession.begin(tmp_path, config)
    for plant in config.active_plants:
        session.record_tag(plant.plant_id, TagObservation(plant.tag_id))
        session.record_water_offset(plant.plant_id, 0.12)
    for group_id in config.groups:
        session.record_station(group_id, {"x": 1.0, "y": 2.0, "angle": 0.0}, source="agv_status")
    with pytest.raises(ValueError, match="C-01"):
        session.publish(tmp_path / "field_height_setup.json")


def test_setup_manual_station_source_cannot_be_published_as_field_setup(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession

    config = HeightTestConfig.from_project(_config())
    session = SetupSession.begin(tmp_path, config)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    for plant in config.plants:
        session.record_tag(plant.plant_id, TagObservation(plant.tag_id))
        if plant.plant_id in config.active_plant_ids:
            session.record_water_offset(plant.plant_id, 0.12)
    for group_id in config.groups:
        session.record_station(group_id, {"x": 1.0, "y": 2.0, "angle": 0.0}, source="manual")
    with pytest.raises(ValueError, match="AGV"):
        session.publish(tmp_path / "field_height_setup.json")


def test_setup_station_can_save_operator_photo(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig
    from wenshi_patrol.height_test.setup import SetupSession

    session = SetupSession.begin(tmp_path, HeightTestConfig.from_project(_config()))
    image = np.zeros((12, 16, 3), dtype=np.uint8)
    session.record_station("left-01", {"x": 1.0, "y": 2.0, "angle": 0.0}, photo=image)
    record = session.stations["left-01"]
    assert record["photo"] == "stations/left-01.jpg"
    assert (tmp_path / "stations" / "left-01.jpg").is_file()


def test_setup_keeps_tag_zero_as_separate_calibration_board(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig
    from wenshi_patrol.height_test.setup import SetupSession

    session = SetupSession.begin(tmp_path, HeightTestConfig.from_project(_config()))
    image = np.zeros((12, 16, 3), dtype=np.uint8)
    session.record_calibration_board(image)
    assert session.calibration_board["tag_id"] == 0
    assert session.calibration_board["photo"] == "reference/tag-0-calibration-board.jpg"
    assert (tmp_path / "reference" / "tag-0-calibration-board.jpg").is_file()


def test_published_setup_rewrites_evidence_paths_from_setup_directory(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession

    config = HeightTestConfig.from_project(_config())
    setup_root = tmp_path / "setup_20260917"
    session = SetupSession.begin(setup_root, config)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    session.record_calibration_board(image)
    for plant in config.plants:
        session.record_tag(plant.plant_id, TagObservation(plant.tag_id), photo=image)
        if plant.plant_id in config.active_plant_ids:
            session.record_water_offset(plant.plant_id, 0.12)
    for group_id in config.groups:
        session.record_station(group_id, {"x": 1.0, "y": 2.0, "angle": 0.0}, photo=image, source="agv_status")
    destination = tmp_path / "field_height_setup.json"
    session.publish(destination)
    value = json.loads(destination.read_text(encoding="utf-8"))
    assert (destination.parent / value["calibration_board"]["photo"]).is_file()
    assert (destination.parent / value["plants"]["A-01"]["photo"]).is_file()
    assert (destination.parent / value["stations"]["left-01"]["photo"]).is_file()
