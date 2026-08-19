import math

from wenshi_patrol.control.route_math import (
    compute_segment_velocity,
    endpoint_reached,
    make_segments,
    segment_progress,
)


STATIONS = {
    "LM1": (-1.248, 0.097, 0.0),
    "LM2": (-1.248, -2.334, 0.0),
    "LM3": (3.313, -2.334, 0.0),
    "LM4": (3.313, 0.097, 0.0),
}


def test_make_segments_uses_wens1_order():
    segments = make_segments(STATIONS, ["LM1", "LM4", "LM3", "LM2"])
    assert [(item.start_name, item.end_name) for item in segments] == [
        ("LM1", "LM4"),
        ("LM4", "LM3"),
        ("LM3", "LM2"),
    ]


def test_segment_yaws_match_wens1_rectangle():
    segments = make_segments(STATIONS, ["LM1", "LM4", "LM3", "LM2"])
    assert segment_progress({"x": -1.0, "y": 0.097, "angle": 0.0}, segments[0], 0.8).segment_yaw == 0.0
    assert segment_progress({"x": 3.313, "y": 0.0, "angle": -math.pi / 2}, segments[1], 0.8).segment_yaw == -math.pi / 2
    assert abs(segment_progress({"x": 3.0, "y": -2.334, "angle": math.pi}, segments[2], 0.8).segment_yaw) == math.pi


def test_velocity_rotates_before_forward_motion_on_large_heading_error():
    segment = make_segments(STATIONS, ["LM4", "LM3"])[0]
    vx, w, progress = compute_segment_velocity(
        {"x": 3.313, "y": 0.097, "angle": 0.0}, segment, 0.25, 0.8, 1.6,
        0.35, 0.04, math.radians(30), math.radians(10), 0.2,
    )
    assert vx == 0.0
    assert w < 0.0
    assert progress.heading_error < 0.0


def test_endpoint_reached_requires_near_target():
    segment = make_segments(STATIONS, ["LM1", "LM4"])[0]
    assert endpoint_reached({"x": 3.28, "y": 0.10, "angle": 0.0}, segment, 0.10)
    assert not endpoint_reached({"x": 2.90, "y": 0.10, "angle": 0.0}, segment, 0.10)

