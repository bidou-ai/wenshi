import pytest

from wenshi_patrol.fixed_approach import (
    bounded_composition_delta,
    validate_home_safe,
)


def test_composition_correction_only_uses_j4_j5_j6():
    delta = bounded_composition_delta("left", (0.2, -0.1), {"j4_deg": 2, "j5_deg": 3, "j6_deg": 1})
    assert set(delta) == {3, 4, 5}
    assert all(abs(value) <= 3 for value in delta.values())


def test_composition_correction_rejects_unknown_side():
    with pytest.raises(ValueError, match="side"):
        bounded_composition_delta("middle", (0.1, 0.1), {})


def test_validate_home_safe_reports_missing_pose():
    assert any("home_safe" in error for error in validate_home_safe({"camera": {"joint": [0] * 6}}))
