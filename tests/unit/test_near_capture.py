import cv2
import numpy as np

import wenshi_patrol.near_capture as near_capture
from wenshi_patrol.near_capture import accept_near_frame, choose_best_frame
from wenshi_patrol.vision.detector import Detection
from wenshi_patrol.vision.targeting import DepthSummary


def test_choose_best_frame_returns_one_frame_only():
    image = np.full((240, 320, 3), 100, dtype=np.uint8)
    cv2.line(image, (50, 40), (260, 210), (255, 255, 255), 4)
    detection = Detection(160, 120, 150, 170, 0.9, class_name="rice")
    frames = [(image, detection, DepthSummary(1.0, 0.01, 1.0, 50, True)), (image.copy(), detection, None)]
    result = choose_best_frame(frames, rounds=3, burst_count=5)
    assert result.image is image
    assert result.quality.ok is True


def test_bad_near_burst_retries_in_current_photo_hold_before_failing():
    assert near_capture.near_burst_action(False, completed_rounds=0, max_rounds=3) == "retry_hold"
    assert near_capture.near_burst_action(False, completed_rounds=1, max_rounds=3) == "retry_hold"
    assert near_capture.near_burst_action(False, completed_rounds=2, max_rounds=3) == "fail"
    assert near_capture.near_burst_action(True, completed_rounds=0, max_rounds=3) == "save"


def test_near_burst_requires_a_new_fresh_camera_frame():
    assert accept_near_frame(None, 10.0, 0.1, 1.0) is True
    assert accept_near_frame(10.0, 10.0, 0.1, 1.0) is False
    assert accept_near_frame(10.0, 10.1, 1.5, 1.0) is False
    assert accept_near_frame(10.0, 10.1, 0.1, 1.0) is True
