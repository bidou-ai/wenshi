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


def test_controller_clamps_all_motion_limits_and_test_once_to_five(monkeypatch):
    from wenshi_patrol.arm_demo import ArmDemoController, MotionStep

    client = FakeJaka()
    controller = ArmDemoController(_config(), _setup(), client=client)
    controller.connect()
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

    assert [move[1] for move in client.moves] == [5.0, 5.0, 5.0, 4.0, 4.0, 5.0, 5.0, 5.0]
    assert all(move[2] == 60.0 for move in client.moves)
    assert client.wait_calls == 2 + len(client.moves)


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


def test_connect_rejects_unknown_or_non_home_start():
    from wenshi_patrol.arm_demo import ArmDemoController

    for joint in (None, [10.0] * 6):
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
        with pytest.raises(RuntimeError, match="home_safe|关节"):
            controller.connect()
        assert not client.moves


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


def test_display_visual_staleness_faults_before_quit(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events, health="检测结果已过期")
    gui = _Cv2(events, [ord("q")])

    run_display(controller, vision, tmp_path, cv=gui)

    assert controller.faults == ["检测结果已过期"]
    assert controller.recover_calls == 0


def test_display_window_close_stops_without_home(tmp_path):
    from wenshi_patrol.arm_demo import run_display

    events = []
    controller = _DisplayController(events)
    vision = _FakeVision(events)
    gui = _Cv2(events, [])
    gui.visible = 0.0

    run_display(controller, vision, tmp_path, cv=gui)

    assert events[-3:] == ["controller.stop", "vision.stop", "destroy"]
    assert controller.recover_calls == 0


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
            self.refresh_calls = 0

        def refresh(self):
            self.refresh_calls += 1
            self.joint_seq += 1

        def snapshot(self):
            value = super().snapshot()
            value["tcp"] = [100.0, 0.0, 300.0, 0.0, 0.0, 0.0]
            return value

    class Camera:
        def health(self):
            return {"ok": True, "seq": 1}

        def capture(self):
            from wenshi_patrol.arm_demo_vision import RgbFrame

            return RgbFrame(1, np.zeros((20, 30, 3), dtype=np.uint8), time.monotonic())

    events = []
    gui = _Cv2(events, [13] * len(POSE_NAMES))
    client = SetupArm()
    config = _config()
    config["camera"] = {"server_url": "http://camera.test"}
    runtime = tmp_path / "runtime" / "arm_demo"

    result = run_interactive_setup(
        config,
        runtime / "demo_setup.json",
        runtime,
        client=client,
        camera=Camera(),
        cv=gui,
        input_fn=lambda _prompt: "operator-1",
    )

    assert result == 0
    assert client.refresh_calls == 30
    assert client.moves == []
    assert (runtime / "demo_setup.json").is_file()


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
            camera=object(),
            cv=Gui(),
            input_fn=lambda _prompt: "operator-1",
        )

    assert client.disconnect_calls == 1


def test_main_stops_vision_and_closes_controller_when_connect_fails(monkeypatch, tmp_path):
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
