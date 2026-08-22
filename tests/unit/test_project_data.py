import math
from pathlib import Path

import pytest

from wenshi_patrol.config import load_config, load_viewpoints, require_joint_pose, resolve_config_path
from wenshi_patrol.control.route_math import make_segments
from wenshi_patrol.control.route_policy import ROUTE_ORDER, validate_route
from wenshi_patrol.map_utils import build_occupancy_fields, load_station_poses


ROOT = Path(__file__).resolve().parents[2]


def test_wenshi_config_map_and_arm_viewpoints():
    config = load_config(ROOT / "config" / "wenshi.yaml")
    assert "targets_file" not in config["fixed_approach"]
    assert not (ROOT / "config" / "fixed_targets.json").exists()
    assert tuple(config["route"]["station_order"]) == ROUTE_ORDER
    assert validate_route(config["route"]["station_order"]) == ROUTE_ORDER
    map_path = resolve_config_path(config, config["map"]["smap_file"])
    stations = load_station_poses(map_path)
    segments = make_segments(stations, list(ROUTE_ORDER))
    assert [(item.start_name, item.end_name) for item in segments] == [
        ("LM1", "LM4"), ("LM4", "LM3"), ("LM3", "LM2")
    ]
    viewpoints = load_viewpoints(config)
    for name in ("camera", "camera_left", "camera_right"):
        assert len(require_joint_pose(viewpoints, name)) == 6


def test_wenshi_map_has_expected_segment_distances():
    config = load_config(ROOT / "config" / "wenshi.yaml")
    stations = load_station_poses(resolve_config_path(config, config["map"]["smap_file"]))
    segments = make_segments(stations, list(ROUTE_ORDER))
    distances = [math.hypot(item.end[0] - item.start[0], item.end[1] - item.start[1]) for item in segments]
    assert distances == pytest.approx([4.561, 2.431, 4.561], abs=0.001)


def test_wenshi_map_builds_occupancy_fields():
    config = load_config(ROOT / "config" / "wenshi.yaml")
    fields = build_occupancy_fields(resolve_config_path(config, config["map"]["smap_file"]))
    assert fields["width"] > 1
    assert fields["height"] > 1
    assert len(fields["data"]) == fields["width"] * fields["height"]
