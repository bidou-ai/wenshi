import cv2
import numpy as np

from wenshi_patrol.near_capture import choose_best_frame
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
