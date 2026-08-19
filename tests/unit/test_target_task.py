import pytest

from wenshi_patrol.controller_math import reverse_target_velocity, MotionOwner
from wenshi_patrol.target_task import TargetTask, TaskObservation


def test_route_cannot_use_reverse_velocity():
    with pytest.raises(ValueError, match="ALIGN_REVERSE"):
        reverse_target_velocity("PATROL_FORWARD", 0.2, 0.1, 0.6)


def test_alignment_velocity_is_capped_and_positive_distance_required():
    assert reverse_target_velocity("ALIGN_REVERSE", 0.2, 0.1, 0.6) == -0.05
    assert reverse_target_velocity("ALIGN_REVERSE", 0.2, 0.0, 0.6) == 0.0
    assert reverse_target_velocity("ALIGN_REVERSE", 0.7, 0.1, 0.6) == 0.0


def test_motion_owner_is_exclusive():
    owner = MotionOwner()
    assert owner.acquire("follow") is True
    assert owner.acquire("fixed") is False
    with pytest.raises(RuntimeError):
        owner.assert_owner("fixed")
    owner.release("follow")
    assert owner.acquire("fixed") is True


def test_target_task_aborts_on_stale_camera():
    task = TargetTask(side="left", reverse_speed_mps=0.05, reverse_limit_m=0.6)
    command = task.tick(TaskObservation(camera_age_s=3.0, target_visible=True, distance_remaining_m=0.5, j5_speed_deg_s=0.0))
    assert command.stop is True
    assert task.state == "ABORT"
