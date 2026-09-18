import numpy as np
import pytest


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


def test_tag_height_uses_topmost_panicle_in_image_coordinates():
    from wenshi_patrol.height_test.algorithms import analyze_view
    from wenshi_patrol.height_test.models import TagObservation
    from wenshi_patrol.vision.detector import Detection

    result = analyze_view(
        _packet(),
        Detection(20, 20, 20, 30, .9),
        [Detection(20, 5, 8, 6, .9), Detection(20, 15, 8, 6, .95)],
        TagObservation(1, tvec=(0.0, 0.0, 1.0), pose_valid=True),
        0.0,
    )
    assert result.methods["tag_panicle_3d"].value_m == pytest.approx(0.45)


def test_fuse_views_rejects_large_within_method_view_disagreement():
    from wenshi_patrol.height_test.algorithms import AlgorithmParams, fuse_views
    from wenshi_patrol.height_test.models import MethodResult, ViewAnalysis

    views = [
        ViewAnalysis({"depth_roi_envelope": MethodResult("depth_roi_envelope", value)})
        for value in (0.50, 0.52, 0.70)
    ]
    result = fuse_views(views, AlgorithmParams(view_disagreement_m=0.08), plant_id="A-01")
    assert result.quality == "needs_review"
    assert "view_disagreement" in result.reasons
