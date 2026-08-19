import cv2
import numpy as np

from wenshi_patrol.vision.detector import Detection
from wenshi_patrol.vision.quality import score_frame
from wenshi_patrol.vision.targeting import DepthSummary


def test_quality_accepts_clear_centered_complete_target():
    image = np.full((240, 320, 3), 100, dtype=np.uint8)
    cv2.line(image, (70, 40), (240, 210), (255, 255, 255), 4)
    detection = Detection(160, 125, 180, 180, 0.9, class_name="rice")
    result = score_frame(image, detection, DepthSummary(1.0, 0.01, 1.0, 100, True), expected_upper_body=True)
    assert result.ok is True
    assert result.score > 0


def test_quality_rejects_edge_clipped_target():
    image = np.full((240, 320, 3), 100, dtype=np.uint8)
    detection = Detection(15, 120, 80, 180, 0.9, class_name="rice")
    result = score_frame(image, detection, None, expected_upper_body=True)
    assert result.ok is False
    assert any("边缘" in reason for reason in result.reasons)
