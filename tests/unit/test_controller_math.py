import math

import pytest

import wenshi_patrol.controller_math as controller_math
from wenshi_patrol.controller_math import (
    BACKWARD_TO_LM4,
    FORWARD_TO_LM5,
    compute_velocity,
    endpoint_approach_speed,
    normalize_angle,
    reverse_motion_allowed,
    slew_rate,
    unverified_reverse_override_active,
)


def test_reverse_requires_verified_radar_and_explicit_enable():
    assert reverse_motion_allowed({}) is False
    assert reverse_motion_allowed({"rear_radar_verified": True}) is False
    assert reverse_motion_allowed({"reverse_motion_enabled": True}) is False
    assert reverse_motion_allowed({"rear_radar_verified": True, "reverse_motion_enabled": True})
    override = {
        "rear_radar_verified": False,
        "reverse_motion_enabled": True,
        "allow_unverified_reverse": True,
    }
    assert reverse_motion_allowed(override)
    assert unverified_reverse_override_active(override)


def test_reverse_uses_wenshi_configuration_key():
    assert reverse_motion_allowed(
        {"rear_radar_verified": True, "reverse_motion_allowed": True}
    )


def test_target_patrol_always_requires_fresh_camera_on_route():
    assert controller_math.route_camera_required({}, target_enabled=True) is True
    assert controller_math.route_camera_required({"required_for_route": True}, target_enabled=False) is True
    assert controller_math.route_camera_required({"required_for_route": False}, target_enabled=False) is False


def test_reverse_distance_accepts_zero_as_a_real_start_position():
    assert controller_math.reverse_distance_travelled(0.0, -0.2) == pytest.approx(0.2)
    assert controller_math.reverse_distance_travelled(1.0, 1.1) == 0.0


def test_endpoint_speed_slows_before_stop():
    arguments = {
        "cruise_speed_mps": 0.1,
        "slowdown_distance_m": 0.3,
        "stop_distance_m": 0.1,
        "minimum_speed_mps": 0.025,
    }
    assert endpoint_approach_speed(0.4, **arguments) == 0.1
    assert endpoint_approach_speed(0.2, **arguments) == pytest.approx(0.0625)
    assert endpoint_approach_speed(0.1, **arguments) == pytest.approx(0.025)


def test_slew_rate_and_cross_track_keep_motion_bounded():
    assert slew_rate(0.0, 0.1, 0.08, 0.05) == 0.004
    assert slew_rate(0.1, -0.1, 0.08, 0.05) == 0.096
    status = {"y": 0.042, "angle": -math.pi}
    forward = compute_velocity(status, FORWARD_TO_LM5, 0.1, -0.058, -math.pi, 1.2, 1.5, 0.15)
    backward = compute_velocity(status, BACKWARD_TO_LM4, 0.1, -0.058, -math.pi, 1.2, 1.5, 0.15)
    assert forward[0] > 0 and forward[1] > 0
    assert backward[0] < 0 and backward[1] < 0


def test_angle_normalization():
    assert abs(normalize_angle(3 * math.pi) - math.pi) < 1e-6
