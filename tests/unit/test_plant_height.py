import math

import pytest

from wenshi_patrol.phenotyping.plant_height import (
    compute_height_candidate,
    polyline_arc_length,
    recompute_reviewed_height,
    resolve_compensation,
)


def test_polyline_arc_length_supports_straight_and_curved_3d_paths():
    assert polyline_arc_length([(0, 0, 0), (0, 0, 1), (0, 0, 2.5)]) == pytest.approx(2.5)
    assert polyline_arc_length([(0, 0, 0), (1, 0, 0), (1, 2, 0)]) == pytest.approx(3.0)


def test_compensation_uses_plant_then_region_then_global_precedence():
    assert resolve_compensation(0.10, {"A": 0.20}, {"A-01": 0.30}, "A-01", "A") == pytest.approx(0.30)
    assert resolve_compensation(0.10, {"A": 0.20}, {}, "A-01", "A") == pytest.approx(0.20)
    assert resolve_compensation(0.10, {}, {}, "A-01", "A") == pytest.approx(0.10)
    assert resolve_compensation(None, {}, {}, "A-01", "A") is None


def test_compute_candidate_adds_compensation_and_preserves_traceable_values():
    result = compute_height_candidate(
        [(0, 0, 0), (0, 0, 0.8)],
        slot_to_water_offset_m=0.2,
        quality={"visible_path_ratio": 1.0, "views_used": ["center"]},
    )
    assert result["trait"] == "plant_height"
    assert result["auto_value_m"] == pytest.approx(1.0)
    assert result["reviewed_value_m"] is None
    assert result["difference_m"] is None
    assert result["path_length_from_slot_m"] == pytest.approx(0.8)
    assert result["slot_to_water_offset_m"] == pytest.approx(0.2)
    assert result["quality"] == "ok"


@pytest.mark.parametrize(
    "path,quality,reason",
    [
        ([(0, 0, 0), (0, 0, float("nan"))], {}, "insufficient_depth"),
        ([(0, 0, 0)], {"occluded": True}, "occlusion"),
        ([(0, 0, 0), (0, 0, 1)], {"path_ambiguous": True}, "ambiguous_path"),
    ],
)
def test_invalid_or_uncertain_candidate_requires_review(path, quality, reason):
    result = compute_height_candidate(path, 0.2, quality)
    assert result["auto_value_m"] is None
    assert result["quality"] == "needs_review"
    assert reason in result["reasons"]


def test_out_of_range_candidate_requires_review_without_fabricating_value():
    result = compute_height_candidate([(0, 0, 0), (0, 0, 4.0)], 0.2, {}, min_height_m=0.1, max_height_m=3.0)
    assert result["auto_value_m"] is None
    assert result["quality"] == "needs_review"
    assert "out_of_range" in result["reasons"]


def test_missing_compensation_requires_review():
    result = compute_height_candidate([(0, 0, 0), (0, 0, 0.8)], None, {})
    assert result["auto_value_m"] is None
    assert result["quality"] == "needs_review"
    assert "missing_compensation" in result["reasons"]


def test_recompute_reviewed_height_keeps_auto_value_and_calculates_difference():
    result = recompute_reviewed_height(
        reviewed_path_3d=[(0, 0, 0), (0, 0, 0.9)],
        compensation=0.2,
        automatic={"auto_value_m": 1.0, "auto_path_3d": [(0, 0, 0), (0, 0, 0.8)]},
        reviewed_by="operator",
    )
    assert result["auto_value_m"] == pytest.approx(1.0)
    assert result["reviewed_value_m"] == pytest.approx(1.1)
    assert result["difference_m"] == pytest.approx(0.1)
    assert result["reviewed_path_3d"] == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.9]]
    assert result["reviewed_by"] == "operator"


def test_reviewed_height_reuses_range_guard_and_records_the_compensation_used():
    result = recompute_reviewed_height(
        reviewed_path_3d=[(0, 0, 0), (0, 0, 4.0)],
        compensation=0.2,
        automatic={"auto_value_m": 1.0, "slot_to_water_offset_m": 0.1},
        min_height_m=0.1,
        max_height_m=3.0,
    )

    assert result["reviewed_value_m"] is None
    assert result["difference_m"] is None
    assert result["slot_to_water_offset_m"] == pytest.approx(0.2)
    assert result["quality"] == "needs_review"
    assert "out_of_range" in result["reasons"]


def test_non_finite_inputs_never_produce_finite_formal_values():
    result = compute_height_candidate([(0, 0, 0), (0, 0, math.inf)], 0.2, {})
    assert result["auto_value_m"] is None
    assert result["difference_m"] is None
    assert all(value is None or math.isfinite(value) for point in result["auto_path_3d"] for value in point)
