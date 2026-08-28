from dataclasses import dataclass

import numpy as np
import pytest


@dataclass
class Frame:
    color: np.ndarray
    depth: np.ndarray | None
    quality: float


class FakeArm:
    def __init__(self, safe=True):
        self.safe = safe
        self.calls = []
        self.failures = set()

    def move_to_view(self, view):
        self.calls.append(("view", view))
        if view in self.failures:
            raise RuntimeError(f"motion failed {view}")

    def move_to_safe(self):
        self.calls.append(("safe",))
        self.safe = True

    def stop_motion(self):
        self.calls.append(("stop",))

    def is_safe_pose(self):
        return self.safe


class FakeCamera:
    def __init__(self, frames_by_view, failures=None):
        self.frames_by_view = frames_by_view
        self.failures = dict(failures or {})
        self.calls = []

    def capture_burst(self, view, count):
        self.calls.append((view, count))
        remaining = self.failures.get(view, 0)
        if remaining:
            self.failures[view] = remaining - 1
            raise RuntimeError(f"failed {view}")
        return self.frames_by_view[view]


class FakeStore:
    def __init__(self, failures=None):
        self.saved = []
        self.failures = set(failures or ())
        self.reviews = []

    def save_capture(self, view, color, depth, frame):
        if view in self.failures:
            raise RuntimeError(f"store failed {view}")
        self.saved.append((view, color, depth, frame))

    def update_review(self, value):
        self.reviews.append(value)


def _matched_tag_decision():
    from wenshi_patrol.phenotyping.schedule import TagDecision

    return TagDecision("matched", expected_tag_id=1, detected_tag_id=1)


def _frames():
    return {
        view: [
            Frame(np.full((2, 2, 3), index, dtype=np.uint8), None, score)
            for index, score in enumerate((0.2, 0.9, 0.4))
        ]
        for view in ("left", "center", "right")
    }


def test_capture_uses_three_views_burst_best_frame_and_safe_pose_gate():
    from wenshi_patrol.phenotyping.capture_controller import CapturePolicy, capture_plant_views

    arm = FakeArm()
    camera = FakeCamera(_frames())
    store = FakeStore()
    report = capture_plant_views(
        "A-01", arm, camera, store, CapturePolicy(burst_count=3),
        base_is_stopped=lambda: True, tag_decision=_matched_tag_decision(),
    )

    assert report.accepted_views == ("left", "center", "right")
    assert [item[0] for item in store.saved] == ["left", "center", "right"]
    assert [item[1][0, 0, 0] for item in store.saved] == [1, 1, 1]
    assert [call[0] for call in arm.calls] == ["view", "view", "view", "safe"]
    assert all(count == 3 for _, count in camera.calls)


def test_capture_refuses_arm_when_base_is_not_stopped():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    arm = FakeArm()
    camera = FakeCamera(_frames())
    store = FakeStore()

    with pytest.raises(RuntimeError, match="base must be stopped"):
        capture_plant_views("A-01", arm, camera, store, base_is_stopped=lambda: False,
                            tag_decision=_matched_tag_decision())

    assert arm.calls == []
    assert camera.calls == []


def test_failed_view_retries_to_cap_then_continues_other_views_and_returns_safe():
    from wenshi_patrol.phenotyping.capture_controller import CapturePolicy, capture_plant_views

    arm = FakeArm()
    camera = FakeCamera(_frames(), failures={"center": 4})
    store = FakeStore()
    report = capture_plant_views(
        "A-01", arm, camera, store,
        CapturePolicy(burst_count=5, max_retries_per_view=3),
        base_is_stopped=lambda: True, tag_decision=_matched_tag_decision(),
    )

    assert report.accepted_views == ("left", "right")
    assert report.missing_views == ("center",)
    assert len([call for call in camera.calls if call[0] == "center"]) == 4
    assert arm.calls[-1] == ("safe",)
    assert "center" in report.retry_reasons


def test_route_resume_requires_completed_stop_and_successful_callback():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    arm = FakeArm()
    camera = FakeCamera(_frames())
    store = FakeStore()
    resumed = []
    capture_plant_views(
        "A-01", arm, camera, store, base_is_stopped=lambda: True,
        tag_decision=_matched_tag_decision(), on_safe_for_route=lambda: resumed.append(True) or True,
        observation_stop_plant_ids=("A-01", "B-L-01"),
        completed_plant_ids=("A-01", "B-L-01"),
    )

    assert resumed == [True]
    assert arm.calls[-1] == ("safe",)


def test_route_resume_is_not_called_until_both_stop_plants_are_complete():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    resumed = []
    capture_plant_views(
        "A-01", FakeArm(), FakeCamera(_frames()), FakeStore(), base_is_stopped=lambda: True,
        tag_decision=_matched_tag_decision(), on_safe_for_route=lambda: resumed.append(True) or True,
        observation_stop_plant_ids=("A-01", "B-L-01"), completed_plant_ids=("A-01",),
    )

    assert resumed == []


def test_route_resume_callback_must_explicitly_confirm_success():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    with pytest.raises(RuntimeError, match="did not confirm success"):
        capture_plant_views(
            "A-01", FakeArm(), FakeCamera(_frames()), FakeStore(), base_is_stopped=lambda: True,
            tag_decision=_matched_tag_decision(), on_safe_for_route=lambda: None,
            observation_stop_plant_ids=("A-01", "B-L-01"),
            completed_plant_ids=("A-01", "B-L-01"),
        )


def test_capture_requires_a_matched_tag_decision_before_arm_motion():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views
    from wenshi_patrol.phenotyping.schedule import TagDecision

    arm = FakeArm()
    with pytest.raises(RuntimeError, match="matched Tag"):
        capture_plant_views(
            "A-01", arm, FakeCamera(_frames()), FakeStore(), base_is_stopped=lambda: True,
            tag_decision=TagDecision("unconfirmed", expected_tag_id=1),
        )
    assert arm.calls == []


def test_missing_base_stop_evidence_fails_closed():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    with pytest.raises(RuntimeError, match="base stop status"):
        capture_plant_views("A-01", FakeArm(), FakeCamera(_frames()), FakeStore(),
                            tag_decision=_matched_tag_decision())


def test_base_is_rechecked_before_each_view_and_failure_is_persisted():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    checks = iter((True, True, True, True, True, False))
    store = FakeStore()
    arm = FakeArm()
    with pytest.raises(RuntimeError, match="base must be stopped"):
        capture_plant_views(
            "A-01", arm, FakeCamera(_frames()), store, base_is_stopped=lambda: next(checks),
            tag_decision=_matched_tag_decision(),
        )

    assert [call for call in arm.calls if call[0] == "safe"] == []
    assert ("stop",) in arm.calls
    assert store.reviews[-1]["failure_reasons"]["right"][0].startswith("RuntimeError: base must be stopped")


def test_arm_motion_failure_stops_arm_without_motion_retry_and_is_persisted():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    arm = FakeArm()
    arm.failures.add("center")
    store = FakeStore()
    with pytest.raises(RuntimeError, match="arm motion failed"):
        capture_plant_views(
            "A-01", arm, FakeCamera(_frames()), store, base_is_stopped=lambda: True,
            tag_decision=_matched_tag_decision(),
        )

    assert [call for call in arm.calls if call[0] == "safe"] == []
    assert [call for call in arm.calls if call == ("view", "center")] == [("view", "center")]
    assert ("stop",) in arm.calls
    assert store.reviews[-1]["failure_reasons"]["center"][0].startswith("arm_motion:")


def test_motion_failure_without_a_stop_operation_is_still_persisted():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    class ArmWithoutStop:
        def move_to_view(self, _view):
            raise RuntimeError("controller offline")

        def move_to_safe(self):
            raise AssertionError("hard-stop failures must not issue another motion command")

    store = FakeStore()
    with pytest.raises(RuntimeError, match="no stop operation"):
        capture_plant_views(
            "A-01", ArmWithoutStop(), FakeCamera(_frames()), store, base_is_stopped=lambda: True,
            tag_decision=_matched_tag_decision(),
        )

    assert store.reviews[-1]["failure_reasons"]["left"][0].startswith("arm_motion:")


def test_single_plant_capture_failure_does_not_prevent_next_plant():
    from wenshi_patrol.phenotyping.capture_controller import capture_plant_views

    first = capture_plant_views(
        "A-01", FakeArm(), FakeCamera(_frames(), failures={"left": 10}), FakeStore(),
        base_is_stopped=lambda: True, tag_decision=_matched_tag_decision(),
    )
    second_store = FakeStore()
    second = capture_plant_views(
        "B-L-01", FakeArm(), FakeCamera(_frames()), second_store,
        base_is_stopped=lambda: True, tag_decision=_matched_tag_decision(),
    )

    assert first.accepted_views == ("center", "right")
    assert second.accepted_views == ("left", "center", "right")
    assert len(second_store.saved) == 3
