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


def test_rgbd_packet_rejects_mismatched_dimensions():
    from wenshi_patrol.height_test.models import FramePacket
    with pytest.raises(ValueError, match="dimensions"):
        FramePacket(np.zeros((4, 4, 3), np.uint8), np.zeros((3, 4), np.uint16), 1, {"fx": 1, "fy": 1})


def test_setup_rejects_duplicate_tag_ids(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession
    session = SetupSession.begin(tmp_path, HeightTestConfig.from_project(_config()))
    session.record_tag("A-01", TagObservation(0))
    with pytest.raises(ValueError, match="duplicate"):
        session.record_tag("A-02", TagObservation(0))


def test_setup_publishes_c_records_and_active_station_coverage(tmp_path):
    from wenshi_patrol.height_test.models import HeightTestConfig, TagObservation
    from wenshi_patrol.height_test.setup import SetupSession
    config = HeightTestConfig.from_project(_config())
    session = SetupSession.begin(tmp_path, config)
    for index, plant_id in enumerate((plant.plant_id for plant in config.plants)):
        session.record_tag(plant_id, TagObservation(index))
        if plant_id in config.active_plant_ids:
            session.record_water_offset(plant_id, 0.12)
    for group_id in config.groups:
        session.record_station(group_id, {"x": 1.0, "y": 2.0, "angle": 0.0})
    destination = tmp_path / "field_height_setup.json"
    session.publish(destination)
    value = json.loads(destination.read_text(encoding="utf-8"))
    assert len(value["active_plants"]) == 24
    assert value["plants"]["C-01"]["excluded_from_detection"] is True
    assert len(value["stations"]) == 16
