import numpy as np


def _packet(depth=1000):
    from wenshi_patrol.height_test.models import FramePacket
    return FramePacket(np.zeros((40, 40, 3), np.uint8), np.full((40, 40), depth, np.uint16), 1, {"fx": 40.0, "fy": 40.0, "cx": 20.0, "cy": 20.0}, profile={"depth_scale": 0.001})


def test_tag_panicle_3d_uses_depth_scale_and_water_offset():
    from wenshi_patrol.height_test.algorithms import analyze_view
    from wenshi_patrol.height_test.models import TagObservation
    from wenshi_patrol.vision.detector import Detection
    result = analyze_view(_packet(), Detection(20, 20, 20, 30, .9), [Detection(20, 5, 8, 8, .9)], TagObservation(0, tvec=(0, .1, 1.0), pose_valid=True), .10)
    assert result.methods["tag_panicle_3d"].value_m is not None
    assert result.methods["bbox_pixel_height"].diagnostic is True


def test_invalid_depth_is_needs_review():
    from wenshi_patrol.height_test.algorithms import analyze_view
    from wenshi_patrol.height_test.models import TagObservation
    from wenshi_patrol.vision.detector import Detection
    result = analyze_view(_packet(0), Detection(20, 20, 20, 30, .9), [Detection(20, 5, 8, 8, .9)], TagObservation(0, tvec=(0, .1, 1.0), pose_valid=True), .10)
    assert result.methods["tag_panicle_3d"].quality == "needs_review"
