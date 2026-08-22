import cv2
import json
import numpy as np

from wenshi_patrol.patrol_target_runtime import PatrolTargetRuntime, RuntimeConfig
from wenshi_patrol.vision.detector import Detection


def _image():
    image = np.full((240, 320, 3), 100, dtype=np.uint8)
    cv2.line(image, (60, 40), (260, 210), (255, 255, 255), 4)
    return image


def _rice(cx=160):
    return Detection(cx, 120, 130, 170, 0.95, class_id=0, class_name="rice")


def test_station_safety_band_blocks_detection():
    runtime = PatrolTargetRuntime(RuntimeConfig(station_safety_band_m=0.5))
    assert runtime.detection_allowed("LM1->LM4", 0.2, 4.0) is False
    assert runtime.detection_allowed("LM1->LM4", 1.0, 4.0) is True
    assert runtime.detection_allowed("LM1->LM4", 3.8, 4.0) is False


def test_stable_target_emits_far_capture_once():
    runtime = PatrolTargetRuntime(RuntimeConfig())
    events = []
    for _ in range(5):
        event = runtime.observe(_image(), [_rice()], 320, 240, "LM1->LM4", 1.0, loop_id=1, now=100.0)
        if event:
            events.append(event)
    assert [item.kind for item in events] == ["far_capture"]
    assert events[0].side == "right"


def test_locked_side_does_not_change():
    runtime = PatrolTargetRuntime(RuntimeConfig())
    for _ in range(5):
        runtime.observe(_image(), [_rice(200)], 320, 240, "LM1->LM4", 1.0, loop_id=1, now=100.0)
    event = runtime.observe(_image(), [_rice(40)], 320, 240, "LM1->LM4", 1.1, loop_id=1, now=101.0)
    assert event is None
    assert runtime.locked_side == "right"


def test_manual_reset_marker_clears_dedupe_once(tmp_path):
    runtime = PatrolTargetRuntime(RuntimeConfig())
    for _ in range(3):
        runtime.observe(_image(), [_rice()], 320, 240, "LM1->LM4", 1.0, loop_id=1, now=100.0)
    runtime.reset_target()
    assert runtime.dedupe.can_process(runtime.locked_key or runtime_key(), 101.0, 1).allowed is False
    marker = tmp_path / "dedupe_reset.json"
    marker.write_text(json.dumps({"request_id": "reset-1"}), encoding="utf-8")

    assert runtime.apply_reset_marker(marker) is True
    assert runtime.apply_reset_marker(marker) is False
    assert runtime.dedupe.can_process(runtime_key(), 101.0, 1).allowed is True


def test_reject_current_target_allows_quality_retry():
    runtime = PatrolTargetRuntime(RuntimeConfig())
    event = None
    for _ in range(3):
        event = runtime.observe(_image(), [_rice()], 320, 240, "LM1->LM4", 1.0, loop_id=1, now=100.0)
    assert event is not None

    runtime.reject_current()

    retried = None
    for _ in range(3):
        retried = runtime.observe(_image(), [_rice()], 320, 240, "LM1->LM4", 1.0, loop_id=1, now=101.0)
    assert retried is not None


def runtime_key():
    from wenshi_patrol.vision.dedupe import TargetKey

    return TargetKey("LM1->LM4", "right", 1.0)
