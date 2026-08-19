import numpy as np

from wenshi_patrol.vision.detector import Detection
from wenshi_patrol.vision.targeting import (
    StableTargetTracker,
    choose_center_target,
    robust_bbox_depth,
    side_from_bbox,
)


def rice(cx, cy=100, width=80, height=200, confidence=0.9):
    return Detection(cx, cy, width, height, confidence, class_id=0, class_name="rice")


def test_center_target_beats_higher_confidence_far_target():
    selected = choose_center_target([rice(100, confidence=0.99), rice(640, confidence=0.5)], 1280, 720)
    assert selected.cx == 640


def test_side_uses_image_left_and_right():
    assert side_from_bbox(rice(100), 1280) == "left"
    assert side_from_bbox(rice(1000), 1280) == "right"


def test_robust_depth_uses_inner_bbox_and_millimeters():
    depth = np.full((300, 800), 1000, dtype=np.uint16)
    depth[80:90, 300:380] = 0
    summary = robust_bbox_depth(depth, rice(340, cy=100, width=100, height=100))
    assert summary.valid
    assert abs(summary.median_m - 1.0) < 1e-6
    assert summary.valid_ratio < 1.0


def test_tracker_requires_three_hits_in_latest_five_frames():
    tracker = StableTargetTracker(window_size=5, min_hits=3, match_distance_ratio=0.15)
    result = None
    for index in range(5):
        result = tracker.observe([rice(500 if index != 1 else 100)], 1000, 720)
    assert result is not None
    assert result.detection.cx == 500
