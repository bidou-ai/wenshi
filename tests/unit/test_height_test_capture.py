import numpy as np
import pytest


def test_model_suite_keeps_plant_and_panicle_models_separate():
    from wenshi_patrol.height_test.capture import ModelSuite
    from wenshi_patrol.vision.detector import Detection

    class Fake:
        def __init__(self, path):
            self.path = str(path)
        def detect(self, image):
            return [Detection(4, 4, 2, 2, .9, class_name=self.path)]

    suite = ModelSuite("plant.pt", "panicle.pt", loader=Fake)
    bundle = suite.infer(np.zeros((10, 10, 3), np.uint8))
    assert bundle.plant_model == "plant.pt"
    assert bundle.panicle_model == "panicle.pt"
    assert bundle.plant and bundle.panicle


def test_best_frame_rejects_burst_without_depth():
    from wenshi_patrol.height_test.capture import select_best_frame
    from wenshi_patrol.height_test.models import FramePacket
    frames = [FramePacket(np.zeros((4, 4, 3), np.uint8), np.zeros((4, 4), np.uint16), 1, {"fx": 1, "fy": 1})]
    with pytest.raises(RuntimeError, match="depth"):
        select_best_frame(frames)
