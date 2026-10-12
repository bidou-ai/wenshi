from __future__ import annotations

from pathlib import Path
import threading
import time

import numpy as np
import pytest


POSE_NAMES = (
    "home_safe",
    "left_clearance",
    "rice1_pre",
    "rice1_center",
    "rice1_scan_a",
    "rice1_scan_b",
    "rice2_pre",
    "rice2_center",
    "rice2_scan_a",
    "rice2_scan_b",
)


def _setup():
    return {
        "poses": {
            name: {"joint": [float(index)] * 6, "tcp": [float(index)] * 6}
            for index, name in enumerate(POSE_NAMES)
        }
    }


def _config():
    return {
        "jaka": {
            "ip": "192.0.2.160",
            "port": 10001,
            "connect_timeout_s": 0.1,
            "joint_tolerance_deg": 0.5,
            "motion_timeout_s": 0.5,
        },
        "arm_demo": {
            "transition_speed_deg_s": 99.0,
            "approach_speed_deg_s": 99.0,
            "scan_speed_deg_s": 99.0,
            "accel_deg_s2": 99.0,
            "home_tolerance_deg": 3.0,
            "joint_state_max_age_s": 0.5,
        },
    }


class FakeJaka:
    def __init__(self, joint=None):
        self.connected = False
        self.joint = list(joint if joint is not None else _setup()["poses"]["home_safe"]["joint"])
        self.joint_seq = 1
        self.moves = []
        self.stop_calls = 0
        self.disconnect_calls = 0
        self.wait_calls = 0
        self.last_error = ""

    def connect(self, timeout=0.0):
        self.connected = True
        return True

    def wait_for_joint_state(self, timeout=0.0):
        self.wait_calls += 1
        self.joint_seq += 1
        return True

    def snapshot(self):
        return {
            "connected": self.connected,
            "joint": list(self.joint),
            "joint_seq": self.joint_seq,
            "joint_status_age": 0.0,
            "error": self.last_error,
        }

    def joint_move(self, target, speed, accel, timeout, *, cancel_requested=None):
        self.moves.append((list(target), float(speed), float(accel), cancel_requested))
        if cancel_requested is not None and cancel_requested():
            self.last_error = "cancelled"
            return False
        self.joint = list(target)
        self.joint_seq += 1
        return True

    def refresh(self):
        self.joint_seq += 1

    def stop(self):
        self.stop_calls += 1

    def disconnect(self):
        self.connected = False
        self.disconnect_calls += 1


def _wait_state(controller, expected, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if controller.snapshot()["state"] == expected:
            return
        time.sleep(0.005)
    assert controller.snapshot()["state"] == expected


def test_lap_path_visits_both_plants_via_clearance_and_reverses_order():
    from wenshi_patrol.arm_demo import build_lap_steps

    first = build_lap_steps(1)
    second = build_lap_steps(2)

    assert first[0].source == "home_safe"
    assert first[-1].target == "home_safe"
    assert [step.target for step in first] == [
        "left_clearance",
        "rice1_pre",
        "rice1_center",
        "rice1_scan_a",
        "rice1_center",
        "rice1_scan_b",
        "rice1_center",
        "rice1_pre",
        "left_clearance",
        "rice2_pre",
        "rice2_center",
        "rice2_scan_a",
        "rice2_center",
        "rice2_scan_b",
        "rice2_center",
        "rice2_pre",
        "left_clearance",
        "home_safe",
    ]
    assert second[1].target == "rice2_pre"
    assert second[9].target == "rice1_pre"
    assert all(left.target == right.source for left, right in zip(first, first[1:]))
    assert sum(step.hold_s for step in first) == pytest.approx(5.2)


def test_controller_clamps_all_motion_limits_and_test_once_to_five(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController, MotionStep

    client = FakeJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    assert controller._speed("transition", False) == 45.0
    assert controller._speed("approach", False) == 30.0
    assert controller._speed("scan", False) == 15.0
    monkeypatch.setattr(
        "wenshi_patrol.arm_demo.build_lap_steps",
        lambda _first: [
            MotionStep("home_safe", "left_clearance", "transition", 0.0),
            MotionStep("left_clearance", "rice1_pre", "approach", 0.0),
            MotionStep("rice1_pre", "rice1_center", "approach", 0.0),
            MotionStep("rice1_center", "rice1_scan_a", "scan", 0.0),
            MotionStep("rice1_scan_a", "rice1_center", "scan", 0.0),
            MotionStep("rice1_center", "rice1_pre", "approach", 0.0),
            MotionStep("rice1_pre", "left_clearance", "approach", 0.0),
            MotionStep("left_clearance", "home_safe", "transition", 0.0),
        ],
    )

    controller.start(test_once=True)
    _wait_state(controller, "READY")

    assert [move[1] for move in client.moves] == [5.0] * 8
    assert all(move[2] == 60.0 for move in client.moves)
    assert client.wait_calls == 2 + len(client.moves)


def test_completed_lap_logs_both_plant_progress_in_motion_order(monkeypatch):
    from wenshi_patrol import arm_demo

    original_build_lap_steps = arm_demo.build_lap_steps
    monkeypatch.setattr(
        arm_demo,
        "build_lap_steps",
        lambda first: [
            arm_demo.MotionStep(step.source, step.target, step.speed_class)
            for step in original_build_lap_steps(first)
        ],
    )
    messages = []
    controller = arm_demo.ArmDemoController(
        _config(), _setup(), client=FakeJaka(), log=messages.append
    )
    controller.connect()

    controller.start(test_once=True)
    _wait_state(controller, "READY")

    assert messages == [
        "开始观察水稻1",
        "水稻1观察完成，前往水稻2",
        "水稻2观察完成",
        "本轮完成：第1轮",
    ]


@pytest.mark.parametrize(
    ("section", "name", "value"),
    [
        ("arm_demo", "transition_speed_deg_s", float("nan")),
        ("arm_demo", "accel_deg_s2", float("inf")),
        ("jaka", "motion_timeout_s", float("nan")),
    ],
)
def test_controller_rejects_non_finite_motion_limits(section, name, value):
    from wenshi_patrol.arm_demo import ArmDemoController

    config = _config()
    config[section][name] = value

    with pytest.raises(ValueError, match="有限|finite"):
        ArmDemoController(config, _setup(), client=FakeJaka())


def test_connect_rejects_unknown_start():
    from wenshi_patrol.arm_demo import ArmDemoController

    for joint in (None, [40.0] * 6):
        client = FakeJaka(joint=[0.0] * 6)
        if joint is None:
            client.snapshot = lambda: {
                "connected": True,
                "joint": None,
                "joint_seq": 2,
                "joint_status_age": 0.0,
            }
        else:
            client.joint = joint
        controller = ArmDemoController(_config(), _setup(), client=client)
        with pytest.raises(RuntimeError, match="安全接入|关节"):
            controller.connect()
        assert not client.moves


def test_connect_near_taught_non_home_pose_enters_hold_and_recovers_via_parents():
    from wenshi_patrol.arm_demo import ArmDemoController

    setup = _setup()
    client = FakeJaka(joint=[10.0] * 6)
    controller = ArmDemoController(_config(), setup, client=client)

    controller.connect()

    assert controller.snapshot()["state"] == "HOLD"
    assert controller.snapshot()["current_pose"] == "rice2_scan_b"
    assert client.moves == []

    controller.recover_home()
    _wait_state(controller, "READY")

    assert [move[0] for move in client.moves] == [
        setup["poses"]["rice2_center"]["joint"],
        setup["poses"]["rice2_pre"]["joint"],
        setup["poses"]["left_clearance"]["joint"],
        setup["poses"]["home_safe"]["joint"],
    ]
    assert [move[1] for move in client.moves] == [15.0, 20.0, 20.0, 20.0]


def test_connect_on_taught_edge_recovers_to_parent_then_home():
    from wenshi_patrol.arm_demo import ArmDemoController

    setup = _setup()
    joints = {
        "home_safe": [100.0] * 6,
        "left_clearance": [80.0] * 6,
        "rice1_pre": [-60.0] * 6,
        "rice1_center": [-40.0] * 6,
        "rice1_scan_a": [-20.0, -60.0, -20.0, -20.0, -20.0, -20.0],
        "rice1_scan_b": [-60.0, -20.0, -60.0, -60.0, -60.0, -60.0],
        "rice2_pre": [40.0, 40.0, 0.0, 0.0, 0.0, 0.0],
        "rice2_center": [20.0, 20.0, 0.0, 0.0, 0.0, 0.0],
        "rice2_scan_a": [-20.0, 20.0, 0.0, 0.0, 0.0, 0.0],
        "rice2_scan_b": [20.0, -20.0, 0.0, 0.0, 0.0, 0.0],
    }
    for name, joint in joints.items():
        setup["poses"][name]["joint"] = joint
    client = FakeJaka(joint=[20.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    controller = ArmDemoController(_config(), setup, client=client)

    controller.connect()

    assert controller.snapshot()["state"] == "HOLD"
    assert controller.snapshot()["current_pose"] is None
    assert client.moves == []

    controller.recover_home()
    _wait_state(controller, "READY")

    assert [move[0] for move in client.moves] == [
        joints["rice2_center"],
        joints["rice2_pre"],
        joints["left_clearance"],
        joints["home_safe"],
    ]
    assert [move[1] for move in client.moves] == [15.0, 20.0, 20.0, 20.0]


def test_connect_within_twenty_degree_bridge_joins_nearest_pose_then_recovers():
    from wenshi_patrol.arm_demo import ArmDemoController

    setup = _setup()
    client = FakeJaka(joint=[22.0] * 6)
    controller = ArmDemoController(_config(), setup, client=client)

    location = controller.connect()

    assert "rice2_scan_b" in location
    assert controller.snapshot()["state"] == "HOLD"
    assert controller.snapshot()["current_pose"] is None
    assert client.moves == []

    controller.recover_home()
    _wait_state(controller, "READY")

    assert [move[0] for move in client.moves] == [
        setup["poses"]["rice2_scan_b"]["joint"],
        setup["poses"]["rice2_center"]["joint"],
        setup["poses"]["rice2_pre"]["joint"],
        setup["poses"]["left_clearance"]["joint"],
        setup["poses"]["home_safe"]["joint"],
    ]
    assert [move[1] for move in client.moves] == [15.0, 15.0, 20.0, 20.0, 20.0]


def test_connect_rejects_ambiguous_taught_pose_match():
    from wenshi_patrol.arm_demo import ArmDemoController

    setup = _setup()
    setup["poses"]["rice2_scan_a"]["joint"] = [9.0] * 6
    client = FakeJaka(joint=[9.0] * 6)
    controller = ArmDemoController(_config(), setup, client=client)

    with pytest.raises(RuntimeError, match="无法唯一匹配"):
        controller.connect()

    assert client.moves == []


def test_start_uses_same_three_degree_home_gate_as_connect(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController

    client = FakeJaka(joint=[1.0] * 6)
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    monkeypatch.setattr("wenshi_patrol.arm_demo.build_lap_steps", lambda _first: [])

    controller.start(test_once=True)
    _wait_state(controller, "READY")

    assert client.moves == []


def test_pause_cancels_active_move_without_sending_a_followup(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController, MotionStep

    entered = threading.Event()

    class BlockingJaka(FakeJaka):
        def joint_move(self, target, speed, accel, timeout, *, cancel_requested=None):
            self.moves.append((list(target), speed, accel, cancel_requested))
            entered.set()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not cancel_requested():
                time.sleep(0.001)
            self.last_error = "机械臂运动已停止"
            return False

    client = BlockingJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    monkeypatch.setattr(
        "wenshi_patrol.arm_demo.build_lap_steps",
        lambda _first: [
            MotionStep("home_safe", "left_clearance", "transition", 0.0),
            MotionStep("left_clearance", "rice1_pre", "approach", 0.0),
        ],
    )
    controller.start()
    assert entered.wait(0.5)

    controller.pause()
    _wait_state(controller, "HOLD")
    time.sleep(0.03)

    assert len(client.moves) == 1
    assert client.stop_calls == 1


def test_hardware_failure_concurrent_with_pause_escalates_hold_to_error(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController, MotionStep

    entered = threading.Event()
    release = threading.Event()

    class ConcurrentFailureJaka(FakeJaka):
        def joint_move(self, target, speed, accel, timeout, *, cancel_requested=None):
            self.moves.append((list(target), speed, accel, cancel_requested))
            entered.set()
            release.wait(1.0)
            self.last_error = "servo fault"
            return False

    client = ConcurrentFailureJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    monkeypatch.setattr(
        "wenshi_patrol.arm_demo.build_lap_steps",
        lambda _first: [MotionStep("home_safe", "left_clearance", "transition")],
    )
    controller.start()
    assert entered.wait(0.5)

    controller.pause()
    release.set()
    _wait_state(controller, "ERROR")

    assert client.stop_calls >= 2
    assert "servo fault" in controller.snapshot()["error"]


def test_unexpected_move_false_calls_stop_and_enters_error(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController, MotionStep

    class FailingJaka(FakeJaka):
        def joint_move(self, target, speed, accel, timeout, *, cancel_requested=None):
            self.moves.append((list(target), speed, accel, cancel_requested))
            self.last_error = "controller rejected move"
            return False

    client = FailingJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    monkeypatch.setattr(
        "wenshi_patrol.arm_demo.build_lap_steps",
        lambda _first: [MotionStep("home_safe", "left_clearance", "transition", 0.0)],
    )
    controller.start()
    _wait_state(controller, "ERROR")

    assert client.stop_calls >= 1
    assert "controller rejected move" in controller.snapshot()["error"]


def test_hold_recovery_uses_active_edge_source_then_taught_parents(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController, MotionStep

    entered = threading.Event()

    class BlockingSecondMove(FakeJaka):
        blocked = False

        def joint_move(self, target, speed, accel, timeout, *, cancel_requested=None):
            self.moves.append((list(target), speed, accel, cancel_requested))
            if len(self.moves) == 2 and not self.blocked:
                self.blocked = True
                entered.set()
                while not cancel_requested():
                    time.sleep(0.001)
                self.last_error = "机械臂运动已停止"
                return False
            self.joint = list(target)
            self.joint_seq += 1
            return True

    client = BlockingSecondMove()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    monkeypatch.setattr(
        "wenshi_patrol.arm_demo.build_lap_steps",
        lambda _first: [
            MotionStep("home_safe", "left_clearance", "transition", 0.0),
            MotionStep("left_clearance", "rice1_pre", "approach", 0.0),
        ],
    )
    controller.start()
    assert entered.wait(0.5)
    controller.pause()
    _wait_state(controller, "HOLD")

    controller.recover_home()
    _wait_state(controller, "READY")

    targets = [move[0] for move in client.moves]
    assert targets[-1] == _setup()["poses"]["home_safe"]["joint"]
    assert _setup()["poses"]["rice1_pre"]["joint"] not in targets[2:]


def test_pause_during_recovery_keeps_the_recovery_target_as_anchor():
    from wenshi_patrol.arm_demo import ArmDemoController, _RunContext

    client = FakeJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    with controller._lock:
        context = _RunContext(7, threading.Event())
        controller._epoch = 7
        controller._context = context
        controller._state = "RECOVERING"
        controller._active_edge = ("rice1_pre", "left_clearance")

    controller.pause()

    assert controller.snapshot()["state"] == "HOLD"
    assert controller._recovery_anchor == "left_clearance"
    steps = controller._recovery_steps("left_clearance", include_anchor=True)
    assert steps[0].source == "rice1_pre"
    assert steps[0].target == "left_clearance"


def test_error_state_never_allows_automatic_home():
    from wenshi_patrol.arm_demo import ArmDemoController

    client = FakeJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    controller.fault("vision stale")

    with pytest.raises(RuntimeError, match="ERROR|故障"):
        controller.recover_home()
    assert not client.moves


def test_stop_never_moves_home_and_is_idempotent():
    from wenshi_patrol.arm_demo import ArmDemoController

    client = FakeJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
    controller.stop()
    controller.stop()

    assert not client.moves
    assert client.stop_calls == 1
    assert controller.snapshot()["state"] == "STOPPED"


def test_close_stops_then_disconnects_without_home_motion():
    from wenshi_patrol.arm_demo import ArmDemoController

    client = FakeJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()

    controller.close()

    assert client.stop_calls == 1
    assert client.disconnect_calls == 1
    assert client.moves == []


class _FakeVision:
    def __init__(self, events, *, health=None):
        from wenshi_patrol.arm_demo_vision import VisionResult

        image = np.zeros((20, 30, 3), dtype=np.uint8)
        self.result = VisionResult(1, image, image.copy(), (), 1.0, time.monotonic())
        self.events = events
        self.health = health

    def latest(self):
        return self.result

    def health_error(self, max_age_s):
        return self.health

    def stop(self):
        self.events.append("vision.stop")


class _DisplayController:
    def __init__(self, events):
        self.events = events
        self.faults = []
        self.starts = []
        self.recover_calls = 0
        self.state = "READY"

    def snapshot(self):
        return {"state": self.state, "error": "", "lap": 0, "current_pose": "home_safe"}

    def start(self, test_once=False):
        self.starts.append(test_once)

    def pause(self):
        self.events.append("pause")

    def recover_home(self):
        self.recover_calls += 1

    def fault(self, reason):
        self.faults.append(reason)

    def stop(self):
        self.events.append("controller.stop")


class _Cv2:
    WINDOW_NORMAL = 0
    WND_PROP_FULLSCREEN = 1
    WINDOW_FULLSCREEN = 1
    WND_PROP_VISIBLE = 2
    IMWRITE_JPEG_QUALITY = 1
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0

    def __init__(self, events, keys):
        self.events = events
        self.keys = iter(keys)
        self.frames = []
        self.visible = 1.0

    def namedWindow(self, *_args):
        self.events.append("named")

    def setWindowProperty(self, *_args):
        self.events.append("fullscreen")

    def imshow(self, _name, frame):
        self.frames.append(frame)

    def putText(self, image, *_args):
        return image

    def waitKey(self, _delay):
        return next(self.keys, ord("q"))

    def waitKeyEx(self, _delay):
        return self.waitKey(_delay)

    def getWindowProperty(self, *_args):
        return self.visible

    def destroyWindow(self, *_args):
        self.events.append("destroy")

    def imwrite(self, path, _frame, _params=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"jpeg")
        return True


def test_display_q_stops_controller_then_vision_then_destroys_without_home(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)
    gui = _Cv2(events, [ord("q")])

    result = run_display(controller, vision, tmp_path, cv=gui)

    assert result == 0
    assert events[-3:] == ["controller.stop", "vision.stop", "destroy"]
    assert controller.recover_calls == 0


def test_display_ignores_extended_key_whose_low_byte_matches_q(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)
    gui = _Cv2(events, [0xFF51, ord("q")])

    run_display(controller, vision, tmp_path, cv=gui)

    assert len(gui.frames) == 2


def test_display_logs_exact_exit_key_and_state(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    messages = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)

    run_display(
        controller,
        vision,
        tmp_path,
        cv=_Cv2(events, [ord("q")]),
        log=messages.append,
    )

    assert messages == ["界面退出: 按键 q raw=113 state=READY error=none"]


def test_display_default_mode_autostarts_continuous_patrol_without_space(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)
    gui = _Cv2(events, [-1, ord("q")])

    run_display(controller, vision, tmp_path, auto_start=True, cv=gui)

    assert controller.starts == [False]


def test_display_does_not_exit_on_initial_false_wayland_visibility(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)
    gui = _Cv2(events, [-1, ord("q")])
    gui.visible = 0.0

    run_display(controller, vision, tmp_path, cv=gui)

    assert len(gui.frames) == 2


def test_display_does_not_stop_when_wayland_visibility_flips_false(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)

    class Gui(_Cv2):
        def __init__(self):
            super().__init__(events, [-1, -1, ord("q")])
            self.visibility = iter([1.0, 0.0, 0.0])

        def getWindowProperty(self, *_args):
            return next(self.visibility)

    gui = Gui()
    run_display(controller, vision, tmp_path, auto_start=True, cv=gui)

    assert len(gui.frames) == 3
    assert controller.starts == [False]


def test_display_cleans_up_if_window_creation_fails(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)

    class Gui(_Cv2):
        def namedWindow(self, *_args):
            raise RuntimeError("GUI unavailable")

    with pytest.raises(RuntimeError, match="GUI unavailable"):
        run_display(controller, vision, tmp_path, cv=Gui(events, []))

    assert events == ["controller.stop", "vision.stop"]


def test_display_visual_staleness_warns_and_recovers_without_stopping_arm(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    messages = []
    controller = _DisplayController(events)

    class Vision(_FakeVision):
        def __init__(self):
            super().__init__(events)
            self.health_results = iter(["<urlopen error timed out>", None])

        def health_error(self, max_age_s):
            return next(self.health_results, None)

    gui = _Cv2(events, [-1, ord("q")])

    run_display(
        controller,
        Vision(),
        tmp_path,
        cv=gui,
        log=messages.append,
    )

    assert controller.faults == []
    assert controller.recover_calls == 0
    assert messages == [
        "视觉链路告警（机械臂继续循环并自动重试）: <urlopen error timed out>",
        "视觉链路已恢复",
        "界面退出: 按键 q raw=113 state=READY error=none",
    ]


def test_controller_fault_logs_reason_before_stopping():
    from wenshi_patrol.arm_demo import ArmDemoController

    messages = []
    controller = ArmDemoController(
        _config(), _setup(), client=FakeJaka(), log=messages.append
    )
    controller.connect()

    controller.fault("视觉链路超过 5.0s 未更新")

    assert messages == ["机械臂演示故障停臂: 视觉链路超过 5.0s 未更新"]


def test_arm_demo_visual_grace_exceeds_camera_request_timeout():
    from wenshi_patrol.config import load_config

    config = load_config(Path(__file__).parents[2] / "config" / "arm_demo.yaml")

    assert config["vision"]["max_age_s"] == 5.0
    assert config["vision"]["max_age_s"] > config["camera"]["timeout_s"]


def test_display_space_pauses_recovery(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    controller.state = "RECOVERING"
    vision = _FakeVision(events)
    gui = _Cv2(events, [32, ord("q")])

    run_display(controller, vision, tmp_path, cv=gui)

    assert "pause" in events


def test_display_space_starts_test_once_and_p_is_only_photo_trigger(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)
    gui = _Cv2(events, [ord("p"), 32, ord("q")])

    run_display(controller, vision, tmp_path, test_once=True, cv=gui)

    assert controller.starts == [True]
    assert len(list(tmp_path.glob("*.jpg"))) == 1


def test_interactive_setup_reads_state_and_never_sends_motion(tmp_path):
    from wenshi_patrol.arm_demo import run_interactive_setup

    class SetupArm(FakeJaka):
        def __init__(self):
            super().__init__()
            self.connect_calls = 0

        def connect(self, timeout=0.0):
            self.connect_calls += 1
            return super().connect(timeout)

        def snapshot(self):
            value = super().snapshot()
            value["tcp"] = [100.0, 0.0, 300.0, 0.0, 0.0, 0.0]
            return value

    class Camera:
        def __init__(self):
            self.calls = 0

        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            self.calls += 1
            return RgbFrame(1, np.zeros((20, 30, 3), dtype=np.uint8), time.monotonic())

    events = []
    gui = _Cv2(events, [13] * len(POSE_NAMES))
    client = SetupArm()
    camera = Camera()
    config = _config()
    config["camera"] = {"server_url": "http://camera.test"}
    runtime = tmp_path / "runtime" / "arm_demo"

    result = run_interactive_setup(
        config,
        runtime / "demo_setup.json",
        runtime,
        client=client,
        camera=camera,
        cv=gui,
        input_fn=lambda _prompt: "operator-1",
    )

    assert result == 0
    assert client.connect_calls == len(POSE_NAMES)
    assert client.wait_calls == len(POSE_NAMES) * 4
    assert client.disconnect_calls == len(POSE_NAMES) * 2 + 1
    assert client.moves == []
    assert camera.calls == len(POSE_NAMES) + 1  # one preflight plus one visible frame per pose
    assert (runtime / "demo_setup.json").is_file()


def test_interactive_setup_reconnects_for_each_pose_after_preview_wait(tmp_path, capsys):
    from wenshi_patrol.arm_demo import run_interactive_setup

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((8, 8, 3), dtype=np.uint8), time.monotonic())

    class IdleClosingJaka(FakeJaka):
        def __init__(self):
            super().__init__()
            self.connect_calls = 0

        def connect(self, timeout=0.0):
            self.connect_calls += 1
            self.connected = True
            return True

        def wait_for_joint_state(self, timeout=0.0):
            self.wait_calls += 1
            self.joint_seq += 1
            return True

        def refresh(self):
            # Reproduces the field failure: the setup connection stopped
            # returning packets while the operator was positioning the arm.
            return False

        def snapshot(self):
            value = super().snapshot()
            value["tcp"] = [100.0, 0.0, 300.0, 0.0, 0.0, 0.0]
            return value

    runtime = tmp_path / "runtime" / "arm_demo"
    client = IdleClosingJaka()
    config = _config()
    config["camera"] = {"server_url": "http://camera.test"}

    result = run_interactive_setup(
        config,
        runtime / "demo_setup.json",
        runtime,
        client=client,
        camera=Camera(),
        cv=_Cv2([], [13, ord("q")]),
        input_fn=lambda _prompt: "operator-1",
        sleep_fn=lambda _seconds: None,
    )

    assert result == 2
    assert client.connect_calls == 1  # JAKA is opened only for the Enter-time read
    assert client.wait_calls == 4  # one pose handshake, then three samples
    assert client.moves == []
    assert client.disconnect_calls >= 2
    draft = next(runtime.glob("setup_*/setup_draft.json"))
    assert '"home_safe"' in draft.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "[1/10] home_safe" in output
    assert "预览窗口" in output


def test_interactive_setup_retries_transient_preview_timeout_without_losing_pose(
    tmp_path, capsys
):
    from wenshi_patrol.arm_demo import run_interactive_setup
    from wenshi_patrol.arm_demo_vision import RgbFrame

    class Camera:
        def __init__(self):
            self.calls = 0

        def capture(self):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("frame timed out")
            return RgbFrame(
                self.calls,
                np.zeros((8, 8, 3), dtype=np.uint8),
                time.monotonic(),
            )

    camera = Camera()
    client = FakeJaka()
    runtime = tmp_path / "runtime" / "arm_demo"
    config = _config()
    config["camera"] = {"server_url": "http://camera.test", "rate_hz": 10.0}

    result = run_interactive_setup(
        config,
        runtime / "demo_setup.json",
        runtime,
        client=client,
        camera=camera,
        cv=_Cv2([], [-1, ord("q")]),
        input_fn=lambda _prompt: "operator-1",
        sleep_fn=lambda _seconds: None,
    )

    assert result == 2
    assert camera.calls == 3  # preflight, failed preview, recovered preview
    assert client.wait_calls == 0
    output = capsys.readouterr().out
    assert "frame timed out" in output
    assert "home_safe" in output


def test_interactive_setup_pose_read_failure_stays_open_for_enter_retry(tmp_path, capsys):
    from wenshi_patrol.arm_demo import run_interactive_setup

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((8, 8, 3), dtype=np.uint8), time.monotonic())

    class OneFailedSampleJaka(FakeJaka):
        def __init__(self):
            super().__init__()
            self.wait_results = iter((True, True, False, True, True, True, True))

        def wait_for_joint_state(self, timeout=0.0):
            self.wait_calls += 1
            result = next(self.wait_results)
            if result:
                self.joint_seq += 1
            return result

        def snapshot(self):
            value = super().snapshot()
            value["tcp"] = [100.0, 0.0, 300.0, 0.0, 0.0, 0.0]
            return value

    runtime = tmp_path / "runtime" / "arm_demo"
    client = OneFailedSampleJaka()
    config = _config()
    config["camera"] = {"server_url": "http://camera.test"}

    result = run_interactive_setup(
        config,
        runtime / "demo_setup.json",
        runtime,
        client=client,
        camera=Camera(),
        cv=_Cv2([], [13, 13, ord("q")]),
        input_fn=lambda _prompt: "operator-1",
        sleep_fn=lambda _seconds: None,
    )

    assert result == 2
    draft_path = next(runtime.glob("setup_*/setup_draft.json"))
    assert '"home_safe"' in draft_path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "home_safe 登记失败" in output
    assert "仍停留在 home_safe" in output


def test_interactive_setup_does_not_continue_after_draft_write_failure(
    tmp_path, monkeypatch
):
    from wenshi_patrol.arm_demo import run_interactive_setup
    from wenshi_patrol.arm_demo_setup import ArmDemoSetupSession

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((8, 8, 3), dtype=np.uint8), time.monotonic())

    class SetupJaka(FakeJaka):
        def snapshot(self):
            value = super().snapshot()
            value["tcp"] = [100.0, 0.0, 300.0, 0.0, 0.0, 0.0]
            return value

    real_checkpoint = ArmDemoSetupSession._checkpoint
    checkpoint_calls = 0

    def fail_pose_checkpoint(session):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise OSError("draft disk full")
        return real_checkpoint(session)

    monkeypatch.setattr(ArmDemoSetupSession, "_checkpoint", fail_pose_checkpoint)
    runtime = tmp_path / "runtime" / "arm_demo"
    config = _config()
    config["camera"] = {"server_url": "http://camera.test"}

    with pytest.raises(OSError, match="draft disk full"):
        run_interactive_setup(
            config,
            runtime / "demo_setup.json",
            runtime,
            client=SetupJaka(),
            camera=Camera(),
            cv=_Cv2([], [13, ord("q")]),
            input_fn=lambda _prompt: "operator-1",
            sleep_fn=lambda _seconds: None,
        )

    draft = next(runtime.glob("setup_*/setup_draft.json"))
    assert '"home_safe"' not in draft.read_text(encoding="utf-8")


def test_interactive_setup_disconnects_if_window_creation_fails(tmp_path):
    from wenshi_patrol.arm_demo import run_interactive_setup

    class Gui:
        WINDOW_NORMAL = 0

        @staticmethod
        def namedWindow(*_args):
            raise RuntimeError("GUI unavailable")

        @staticmethod
        def destroyWindow(*_args):
            pass

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((8, 8, 3), dtype=np.uint8), time.monotonic())

    client = FakeJaka()
    runtime = tmp_path / "runtime" / "arm_demo"
    config = _config()
    config["camera"] = {"server_url": "http://camera.test"}

    with pytest.raises(RuntimeError, match="GUI unavailable"):
        run_interactive_setup(
            config,
            runtime / "demo_setup.json",
            runtime,
            client=client,
            camera=Camera(),
            cv=Gui(),
            input_fn=lambda _prompt: "operator-1",
        )

    assert client.disconnect_calls == 1


def test_interactive_setup_preflights_camera_before_jaka_connect(tmp_path):
    from wenshi_patrol.arm_demo import run_interactive_setup

    class Camera:
        def __init__(self):
            self.calls = []

        def health(self):
            self.calls.append("health")
            return {"ok": True, "seq": 1}

        def capture(self):
            self.calls.append("frame")
            raise RuntimeError("JPEG timeout")

    class Client:
        def __init__(self):
            self.connect_calls = 0

        def connect(self, timeout=0.0):
            self.connect_calls += 1
            return True

    camera = Camera()
    client = Client()
    config = _config()
    config["camera"] = {"server_url": "http://d435.test"}

    with pytest.raises(RuntimeError, match=r"D435.*http://d435.test.*frame.*JPEG timeout"):
        run_interactive_setup(
            config,
            tmp_path / "runtime" / "arm_demo" / "demo_setup.json",
            tmp_path / "runtime" / "arm_demo",
            client=client,
            camera=camera,
            input_fn=lambda _prompt: "operator-1",
            sleep_fn=lambda _seconds: None,
        )

    assert camera.calls == ["frame"] * 3
    assert client.connect_calls == 0


def test_interactive_setup_retries_fresh_jaka_state_then_continues(tmp_path):
    from wenshi_patrol.arm_demo import run_interactive_setup

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((8, 8, 3), dtype=np.uint8), time.monotonic())

    class Client:
        def __init__(self):
            self.connect_calls = 0
            self.disconnect_calls = 0
            self.wait_results = iter((False, True, True, True, True))
            self.last_error = "state stale"
            self.joint_seq = 0

        def connect(self, timeout=0.0):
            self.connect_calls += 1
            return True

        def wait_for_joint_state(self, timeout=0.0):
            result = next(self.wait_results)
            if result:
                self.joint_seq += 1
            return result

        def snapshot(self):
            return {
                "connected": True,
                "joint": [0.0] * 6,
                "tcp": [0.0, 0.0, 300.0, 0.0, 0.0, 0.0],
                "joint_seq": self.joint_seq,
                "joint_status_age": 0.0,
                "error": self.last_error,
            }

        def disconnect(self):
            self.disconnect_calls += 1

    sleeps = []
    client = Client()
    config = _config()
    config["camera"] = {"server_url": "http://d435.test"}
    events = []

    result = run_interactive_setup(
        config,
        tmp_path / "runtime" / "arm_demo" / "demo_setup.json",
        tmp_path / "runtime" / "arm_demo",
        client=client,
        camera=Camera(),
        cv=_Cv2(events, [13, ord("q")]),
        input_fn=lambda _prompt: "operator-1",
        sleep_fn=sleeps.append,
    )

    assert result == 2
    assert client.connect_calls == 2
    assert client.disconnect_calls >= 3
    assert sleeps == [0.5]


def test_interactive_setup_reports_jaka_retry_failure_and_keeps_resume_draft(tmp_path, capsys):
    from wenshi_patrol.arm_demo import run_interactive_setup
    from wenshi_patrol.arm_demo_setup import ArmDemoSetupSession

    runtime = tmp_path / "runtime" / "arm_demo"
    evidence = runtime / "setup_existing"
    draft = ArmDemoSetupSession(evidence, runtime)
    draft.set_operator("operator-1")

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((8, 8, 3), dtype=np.uint8), time.monotonic())

    class Client:
        last_error = "connection refused"

        def __init__(self):
            self.connect_calls = 0
            self.disconnect_calls = 0

        def connect(self, timeout=0.0):
            self.connect_calls += 1
            return False

        def disconnect(self):
            self.disconnect_calls += 1

    client = Client()
    config = _config()
    config["camera"] = {"server_url": "http://d435.test"}

    result = run_interactive_setup(
        config,
        runtime / "demo_setup.json",
        runtime,
        resume_root=evidence,
        client=client,
        camera=Camera(),
        cv=_Cv2([], [13, ord("q")]),
        sleep_fn=lambda _seconds: None,
    )

    assert result == 2
    assert client.connect_calls == 3
    assert client.disconnect_calls >= 3
    assert draft.draft_path.is_file()
    output = capsys.readouterr().out
    assert str(evidence) in output
    assert f"./arm_demo.sh --setup --resume {evidence}" in output
    assert "connection refused" in output


def test_main_reports_startup_stage_and_cleans_up_when_connect_fails(
    monkeypatch, tmp_path, capsys
):
    from wenshi_patrol import arm_demo

    events = []

    class Vision:
        fatal_error = None

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            events.append("vision.start")

        def latest(self):
            return object()

        def stop(self):
            events.append("vision.stop")

    class Controller:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self):
            raise RuntimeError("connect failed")

        def close(self):
            events.append("controller.close")

    config = _config()
    config.update(
        {
            "camera": {"server_url": "http://camera.test", "startup_wait_s": 0.1},
            "vision": {"model_path": "model.pt"},
        }
    )
    monkeypatch.setattr(arm_demo, "load_config", lambda _path: config)
    monkeypatch.setattr(arm_demo, "_local_preflight", lambda *_args: _setup())
    monkeypatch.setattr(arm_demo, "SnapshotCamera", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(arm_demo, "PanicleVisionPipeline", Vision)
    monkeypatch.setattr(arm_demo, "ArmDemoController", Controller)
    monkeypatch.setattr(arm_demo, "resolve_config_path", lambda *_args: tmp_path / "model.pt")

    result = arm_demo.main(
        [
            "--config",
            str(tmp_path / "config.yaml"),
            "--setup-file",
            str(tmp_path / "setup.json"),
        ]
    )

    assert result == 1
    assert events == ["vision.start", "controller.close", "vision.stop"]
    output = capsys.readouterr()
    assert "[1/4] 加载稻穗检测模型" in output.out
    assert "[2/4] D435 与检测画面已就绪" in output.out
    assert "[3/4] 连接 JAKA" in output.out
    assert "connect failed" in output.err


def test_main_automatically_recovers_recognized_start_pose_before_display(
    monkeypatch, tmp_path, capsys
):
    from wenshi_patrol import arm_demo

    events = []

    class Vision:
        fatal_error = None

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            events.append("vision.start")

        def latest(self):
            return object()

        def stop(self):
            events.append("vision.stop")

    class Controller:
        def __init__(self, *_args, **_kwargs):
            self.state = "HOLD"

        def connect(self):
            events.append("controller.connect")
            return "rice2_scan_b"

        def snapshot(self):
            return {"state": self.state, "error": ""}

        def recover_home(self):
            events.append("controller.recover_home")
            self.state = "READY"

        def close(self):
            events.append("controller.close")

    config = _config()
    config.update(
        {
            "camera": {"server_url": "http://camera.test", "startup_wait_s": 0.1},
            "vision": {"model_path": "model.pt"},
        }
    )
    monkeypatch.setattr(arm_demo, "load_config", lambda _path: config)
    monkeypatch.setattr(arm_demo, "_local_preflight", lambda *_args: _setup())
    monkeypatch.setattr(arm_demo, "SnapshotCamera", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(arm_demo, "PanicleVisionPipeline", Vision)
    monkeypatch.setattr(arm_demo, "ArmDemoController", Controller)
    monkeypatch.setattr(arm_demo, "resolve_config_path", lambda *_args: tmp_path / "model.pt")

    def display(controller, *_args, **kwargs):
        events.append(
            f"display.{controller.snapshot()['state']}.auto={kwargs.get('auto_start')}"
        )
        return 0

    monkeypatch.setattr(arm_demo, "run_display", display)

    result = arm_demo.main(
        [
            "--config",
            str(tmp_path / "config.yaml"),
            "--setup-file",
            str(tmp_path / "setup.json"),
        ]
    )

    assert result == 0
    assert events == [
        "vision.start",
        "controller.connect",
        "controller.recover_home",
        "display.READY.auto=True",
        "controller.close",
        "vision.stop",
    ]
    assert "自动低速回 home_safe" in capsys.readouterr().out


def test_setup_keyboard_interrupt_exits_cleanly(monkeypatch, tmp_path, capsys):
    from wenshi_patrol import arm_demo

    monkeypatch.setattr(arm_demo, "load_config", lambda _path: _config())

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(arm_demo, "run_interactive_setup", interrupted)

    result = arm_demo.main(
        [
            "--setup",
            "--config",
            str(tmp_path / "config.yaml"),
            "--runtime-root",
            str(tmp_path / "runtime" / "arm_demo"),
        ]
    )

    assert result == 130
    assert "setup 已中止" in capsys.readouterr().err
