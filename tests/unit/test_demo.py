from pathlib import Path
import threading

import numpy as np
import pytest


def _controller(tmp_path):
    from wenshi_patrol.demo import DemoController

    class Status:
        def __init__(self):
            self.stop_calls = 0
            self.value = {"x": 0.0, "y": 0.0, "angle": 0.0, "is_stop": True, "status_age": 0.0, "blocked": False, "emergency": False}
        def get_status(self): return dict(self.value)
        def stop(self): self.stop_calls += 1

    class Motion:
        def __init__(self): self.stop_calls = 0
        def stop(self): self.stop_calls += 1

    class Arm:
        def __init__(self): self.stop_calls = 0; self.safe_calls = 0
        def stop(self): self.stop_calls += 1
        def move_to_safe(self): self.safe_calls += 1

    controller = DemoController.__new__(DemoController)
    controller.config = {
        "safety": {"station_stop_timeout_s": 0.2},
        "demo": {"station_position_tolerance_m": 0.15, "station_heading_tolerance_deg": 10.0},
    }
    controller.status = Status()
    controller.motion = Motion()
    controller.arm = Arm()
    controller.photo_dir = Path(tmp_path)
    controller._stopped = False
    controller._running = False
    controller._photo_index = 0
    controller._lock = threading.Lock()
    controller.current_station = "A-01"
    controller.observation_stations = {"A-01": (0.0, 0.0, 0.0)}
    controller.log = lambda _message: None
    return controller


def test_demo_stop_is_idempotent(tmp_path):
    controller = _controller(tmp_path)
    controller.stop("operator")
    controller.stop("repeat")
    assert controller.motion.stop_calls == 1
    assert controller.arm.stop_calls == 1
    assert controller.arm.safe_calls == 1


def test_demo_stop_skips_arm_retract_when_agv_has_alarm(tmp_path):
    controller = _controller(tmp_path)
    messages = []
    controller.log = messages.append
    controller.status.value["errors"] = ["drive fault"]
    controller.stop("AGV alarm")
    assert controller.motion.stop_calls == 1
    assert controller.arm.stop_calls == 1
    assert controller.arm.safe_calls == 0
    assert any("实体急停" in message for message in messages)


def test_demo_photo_is_written_only_to_demo_photo_directory(tmp_path):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path / "photos")
    controller.camera = lambda: np.zeros((8, 8, 3), dtype=np.uint8)
    output = DemoController.save_photo(controller)
    assert output.parent == (tmp_path / "photos").resolve()
    assert output.suffix == ".jpg"
    assert output.is_file()


def test_demo_observe_failure_still_attempts_safe_retract(tmp_path):
    controller = _controller(tmp_path)

    class FailingArm(controller.arm.__class__):
        def __init__(self):
            super().__init__()
            self.safe_calls = 0

        def move_to_safe(self):
            self.safe_calls += 1
            return True

        def observe(self, _view):
            return False

    controller.arm = FailingArm()
    with pytest.raises(RuntimeError, match="展示观察动作失败"):
        controller._observe_station()
    assert controller.arm.safe_calls == 2


def test_demo_camera_disabled_skips_health_check(tmp_path):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)

    class Status:
        def connect(self):
            return True

        def wait_for_status(self, **_kwargs):
            return True

    class Motion:
        last_error = ""

        def connect(self):
            return True

    class Arm:
        def connect(self):
            return None

    class Camera:
        def __init__(self):
            self.health_calls = 0
            self.color_calls = 0

        def health(self):
            self.health_calls += 1
            return {"ok": True}

        def color(self):
            self.color_calls += 1
            return np.zeros((2, 2, 3), dtype=np.uint8)

    camera = Camera()
    controller.status = Status()
    controller.motion = Motion()
    controller.arm = Arm()
    controller.camera = camera
    controller.camera_enabled = False
    DemoController.connect(controller)
    assert camera.health_calls == 0


def test_demo_start_rejects_agv_that_is_not_stopped(tmp_path):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller.arm.move_to_safe = lambda: (_ for _ in ()).throw(AssertionError("arm moved before AGV stop"))
    controller.status.value["is_stop"] = False
    with pytest.raises(RuntimeError, match="停稳"):
        controller.start()


def test_demo_initial_start_creates_route_thread_when_arm_supports_resume(tmp_path, monkeypatch):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller._pause_event = threading.Event()
    controller._route_thread = None
    controller.arm.move_to_safe = lambda: True
    controller.arm.resume_calls = 0
    controller.arm.resume = lambda: setattr(controller.arm, "resume_calls", controller.arm.resume_calls + 1)

    class Thread:
        def __init__(self, **_kwargs): self.started = False
        def start(self): self.started = True
        def is_alive(self): return self.started

    monkeypatch.setattr("wenshi_patrol.demo.threading.Thread", Thread)
    DemoController.start(controller)
    assert controller.arm.resume_calls == 1
    assert controller._route_thread is not None
    assert controller._route_thread.started is True


def test_demo_fresh_status_rejects_agv_alarm(tmp_path):
    controller = _controller(tmp_path)
    controller.status.value["errors"] = ["drive fault"]
    with pytest.raises(RuntimeError, match="报警"):
        controller._fresh_status()


@pytest.mark.parametrize("field", ("x", "y", "angle"))
def test_demo_fresh_status_rejects_nonfinite_pose(tmp_path, field):
    controller = _controller(tmp_path)
    controller.status.value[field] = float("nan")

    with pytest.raises(RuntimeError, match="无效|非有限"):
        controller._fresh_status()


def test_demo_pause_stops_arm_and_retracts(tmp_path):
    controller = _controller(tmp_path)
    controller._pause_event = threading.Event()
    controller.pause()
    assert controller._pause_event.is_set()
    assert controller.motion.stop_calls == 1
    assert controller.arm.stop_calls == 1
    assert controller.arm.safe_calls == 1


@pytest.mark.parametrize(
    ("field", "value"),
    (("errors", ["drive fault"]), ("fatals", ["fatal"]), ("brake", True)),
)
def test_demo_station_stop_rejects_alarm_before_arm_motion(tmp_path, field, value):
    controller = _controller(tmp_path)
    controller.config = {"safety": {"station_stop_timeout_s": 0.2}}
    controller._stop_event = threading.Event()
    controller.status.wait_for_status = lambda **_kwargs: True
    controller.status.value[field] = value

    class Arm:
        def move_to_safe(self):
            raise AssertionError("arm moved while AGV alarm was active")

        def observe(self, _view):
            raise AssertionError("arm observed while AGV alarm was active")

    controller.arm = Arm()
    with pytest.raises(RuntimeError, match="报警|刹车"):
        controller._observe_station()


def test_demo_station_alignment_failure_blocks_arm_motion(tmp_path):
    controller = _controller(tmp_path)
    controller.config = {
        "safety": {"station_stop_timeout_s": 0.2},
        "demo": {"station_position_tolerance_m": 0.15, "station_heading_tolerance_deg": 10.0},
    }
    controller._stop_event = threading.Event()
    controller.current_station = "A-01"
    controller.observation_stations = {"A-01": (0.0, 0.0, 0.0)}
    controller._align_station_heading = lambda: (_ for _ in ()).throw(RuntimeError("朝向校正超时"))
    messages = []
    controller.log = messages.append

    class Arm:
        def move_to_safe(self):
            raise AssertionError("arm moved with a wrong AGV heading")

        def observe(self, _view):
            raise AssertionError("arm observed with a wrong AGV heading")

    controller.arm = Arm()
    with pytest.raises(RuntimeError, match="朝向"):
        controller._observe_station()
    assert any("姿态校正或校验未通过" in message for message in messages)
    assert not any("未确认安全停稳" in message for message in messages)


def test_demo_setup_rejects_station_that_moves_while_photo_is_taken():
    from wenshi_patrol.demo import _require_setup_pose_stability

    with pytest.raises(RuntimeError, match="拍照期间.*移动"):
        _require_setup_pose_stability(
            {"x": 1.0, "y": 2.0, "angle": 0.0},
            {"x": 1.05, "y": 2.0, "angle": 0.0},
            {"demo": {"setup_position_stability_m": 0.02, "setup_heading_stability_deg": 2.0}},
        )


def test_demo_route_rows_come_from_configured_lm_corners():
    from wenshi_patrol.demo import _demo_route_rows

    top_y, bottom_y = _demo_route_rows({"map": {"smap_file": "../map/wenshi.smap"}, "_config_dir": str(Path("config").resolve())})

    assert top_y == pytest.approx(0.097)
    assert bottom_y == pytest.approx(-2.334)


@pytest.mark.parametrize(
    ("station", "view"),
    (("A-01", "left"), ("B-L-08", "right"), ("B-R-04", "left")),
)
def test_demo_station_uses_only_its_matching_side_view(station, view):
    from wenshi_patrol.demo import demo_view_for_station

    assert demo_view_for_station(station) == view


def test_demo_observes_only_the_station_side(tmp_path):
    controller = _controller(tmp_path)
    controller.current_station = "B-L-01"
    controller.observation_stations = {"B-L-01": (0.0, 0.0, 0.0)}
    observed = []

    class Arm(controller.arm.__class__):
        def move_to_safe(self):
            return True

        def observe(self, view):
            observed.append(view)
            return True

    controller.arm = Arm()

    controller._observe_station()

    assert observed == ["right"]


def test_demo_aligns_station_heading_before_arm_motion(tmp_path, monkeypatch):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller._pause_event = threading.Event()
    controller._running = True
    controller.current_station = "A-06"
    controller.observation_stations = {"A-06": (1.3253, -2.239, __import__("math").pi)}
    controller.config["demo"].update({
        "heading_alignment_tolerance_deg": 3.0,
        "heading_alignment_timeout_s": 3.0,
        "heading_alignment_gain": 2.0,
        "heading_alignment_max_rad_s": 0.45,
        "heading_alignment_min_rad_s": 0.08,
    })
    headings = iter((166.6, 175.0, 178.2, 178.2))
    controller._fresh_status = lambda: {
        "x": 1.3253,
        "y": -2.239,
        "angle": __import__("math").radians(next(headings)),
        "is_stop": False,
    }
    commands = []
    controller.motion.set_velocity = lambda vx, w: commands.append((vx, w))
    monkeypatch.setattr("wenshi_patrol.demo.time.sleep", lambda _seconds: None)

    DemoController._align_station_heading(controller)

    assert commands
    assert all(vx == 0.0 for vx, _w in commands)
    assert all(w > 0.0 for _vx, w in commands)
    assert controller.motion.stop_calls >= 1


def test_demo_heading_alignment_rejects_position_drift(tmp_path):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller.current_station = "A-06"
    controller.observation_stations = {"A-06": (1.0, 2.0, __import__("math").pi)}
    controller.config["demo"].update({
        "station_position_tolerance_m": 0.15,
        "heading_alignment_tolerance_deg": 3.0,
        "heading_alignment_timeout_s": 1.0,
    })
    controller._fresh_status = lambda: {"x": 1.20, "y": 2.0, "angle": 0.0}
    controller.motion.set_velocity = lambda _vx, _w: (_ for _ in ()).throw(AssertionError("must not rotate after drift"))

    with pytest.raises(RuntimeError, match="位置偏差"):
        DemoController._align_station_heading(controller)

    assert controller.motion.stop_calls >= 1


def test_demo_heading_alignment_never_commands_rotation_while_paused(tmp_path, monkeypatch):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller._pause_event = threading.Event()
    controller._pause_event.set()
    controller._running = False
    controller.current_station = "A-06"
    controller.observation_stations = {"A-06": (1.0, 2.0, __import__("math").pi)}
    controller.config["demo"].update({
        "heading_alignment_tolerance_deg": 3.0,
        "heading_alignment_timeout_s": 1.0,
    })
    controller._fresh_status = lambda: {"x": 1.0, "y": 2.0, "angle": 0.0, "is_stop": False}
    commands = []
    controller.motion.set_velocity = lambda vx, w: commands.append((vx, w))

    def interrupt_wait(_seconds):
        controller._stop_event.set()

    monkeypatch.setattr("wenshi_patrol.demo.time.sleep", interrupt_wait)

    with pytest.raises(RuntimeError, match="停止"):
        DemoController._align_station_heading(controller)

    assert commands == []


@pytest.mark.parametrize(
    ("is_stop", "heading_deg", "message"),
    ((False, 0.0, "停稳"), (True, 5.0, "3.0deg")),
)
def test_demo_final_pre_arm_gate_requires_stopped_and_three_degree_heading(
    tmp_path, is_stop, heading_deg, message
):
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller.current_station = "A-06"
    controller.observation_stations = {"A-06": (1.0, 2.0, 0.0)}
    controller.config["demo"].update({
        "station_position_tolerance_m": 0.15,
        "station_heading_tolerance_deg": 10.0,
        "heading_alignment_tolerance_deg": 3.0,
    })
    controller._fresh_status = lambda: {
        "x": 1.0,
        "y": 2.0,
        "angle": __import__("math").radians(heading_deg),
        "is_stop": is_stop,
    }

    with pytest.raises(RuntimeError, match=message):
        DemoController._require_recorded_station_pose(controller)


def test_demo_route_accelerates_and_slows_near_station(tmp_path, monkeypatch):
    from wenshi_patrol.control.route_math import Segment
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller._pause_event = threading.Event()
    controller._running = True
    controller.config.update({
        "control": {
            "endpoint_tolerance_m": 0.10,
            "endpoint_slowdown_distance_m": 0.60,
            "endpoint_min_speed_mps": 0.04,
            "cross_track_gain": 0.8,
            "heading_gain": 1.6,
            "max_angular_speed_rad_s": 0.35,
            "correction_threshold_m": 0.04,
            "rotate_in_place_threshold_deg": 30.0,
            "heading_slowdown_threshold_deg": 10.0,
            "min_heading_scale": 0.20,
        },
        "safety": {"hard_cross_track_m": 0.25},
        "demo": {
            "route_speed_mps": 0.18,
            "endpoint_slowdown_distance_m": 0.60,
            "endpoint_min_speed_mps": 0.04,
        },
    })
    statuses = iter((
        {"x": 0.0, "y": 0.0, "angle": 0.0},
        {"x": 0.7, "y": 0.0, "angle": 0.0},
        {"x": 0.95, "y": 0.0, "angle": 0.0},
    ))
    controller._fresh_status = lambda: next(statuses)
    commands = []
    controller.motion.set_velocity = lambda vx, w: commands.append((vx, w))
    monkeypatch.setattr("wenshi_patrol.demo.time.sleep", lambda _seconds: None)

    assert DemoController._run_segment(
        controller,
        Segment("start", "A-01", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    )

    assert commands[0] == pytest.approx((0.18, 0.0))
    assert 0.04 < commands[1][0] < 0.18
    assert controller.motion.stop_calls >= 1


def test_demo_route_never_commands_velocity_after_pause_state_transition(tmp_path, monkeypatch):
    from wenshi_patrol.control.route_math import Segment
    from wenshi_patrol.demo import DemoController

    controller = _controller(tmp_path)
    controller._stop_event = threading.Event()
    controller._pause_event = threading.Event()
    controller._running = True
    controller.config.update({
        "control": {"endpoint_tolerance_m": 0.10},
        "safety": {"hard_cross_track_m": 0.25},
        "demo": {"route_speed_mps": 0.18},
    })
    segment = Segment("start", "end", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))

    def pause_before_command():
        with controller._lock:
            controller._running = False
            controller._pause_event.set()
        return {"x": 0.0, "y": 0.0, "angle": 0.0, "is_stop": True}

    controller._fresh_status = pause_before_command
    commands = []
    controller.motion.set_velocity = lambda vx, w: commands.append((vx, w))
    monkeypatch.setattr("wenshi_patrol.demo.time.sleep", lambda _seconds: controller._stop_event.set())

    assert DemoController._run_segment(controller, segment) is False
    assert commands == []


def test_demo_arm_uses_faster_observe_and_retract_speeds():
    from wenshi_patrol.demo import DemoArm

    calls = []

    class Client:
        def joint_move(self, target, speed, accel, timeout, **_kwargs):
            calls.append((target, speed, accel, timeout))
            return True

    arm = DemoArm.__new__(DemoArm)
    arm.client = Client()
    arm.poses = {"left": [1.0] * 6}
    arm.safe = [0.0] * 6
    arm.observe_speed = 50.0
    arm.retract_speed = 60.0
    arm.accel = 80.0
    arm.timeout = 120.0
    arm._motion_lock = threading.Lock()
    arm._cancel_event = threading.Event()
    arm.cancel_requested = None

    assert arm.move_to_view("left")
    assert arm.move_to_safe()

    assert calls[0][1:3] == (50.0, 80.0)
    assert calls[1][1:3] == (60.0, 80.0)


def test_demo_setup_preview_displays_and_returns_latest_frame(monkeypatch):
    from wenshi_patrol.demo import DemoSetupPreview

    class Camera:
        def __init__(self):
            self.calls = 0

        def color(self):
            self.calls += 1
            return np.full((6, 8, 3), self.calls, dtype=np.uint8)

    displayed = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("wenshi_patrol.demo.cv2.imshow", lambda title, image: displayed.append((title, image.copy())))
    monkeypatch.setattr("wenshi_patrol.demo.cv2.waitKey", lambda _delay: -1)
    monkeypatch.setattr("wenshi_patrol.demo.cv2.destroyWindow", lambda _title: None)
    preview = DemoSetupPreview(Camera(), startup_timeout_s=1.0)

    preview.start()
    image = preview.color()
    preview.stop()

    assert displayed
    assert displayed[0][0] == "Wenshi Demo Setup - D435 RGB"
    assert image.shape == (6, 8, 3)
    assert int(image[0, 0, 0]) >= 1


def test_demo_setup_ctrl_c_exits_cleanly_without_publishing(tmp_path, monkeypatch, capsys):
    from wenshi_patrol.demo import run_interactive_setup

    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt()))
    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"

    result = run_interactive_setup({}, destination, camera_enabled=False)

    assert result == 130
    assert not destination.exists()
    assert "已取消" in capsys.readouterr().out


def test_demo_setup_legacy_resume_skips_existing_photos_and_prompts_for_tag_19(tmp_path, monkeypatch):
    import cv2

    from wenshi_patrol.demo import run_interactive_setup
    from wenshi_patrol.demo_setup import DEMO_PLANTS

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    root = destination.parent / "setup_20260918_105456"
    image = np.full((24, 32, 3), 127, dtype=np.uint8)
    (root / "reference").mkdir(parents=True)
    (root / "tags").mkdir()
    assert cv2.imwrite(str(root / "reference" / "tag-0-calibration-board.jpg"), image)
    for plant_id, _tag_id, _observed in DEMO_PLANTS[:18]:
        assert cv2.imwrite(str(root / "tags" / f"{plant_id}.jpg"), image)
    answers = iter(("cjc", "q"))
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)

    result = run_interactive_setup({}, destination, camera_enabled=False, resume_root=root)

    assert result == 2
    assert any("B-R-03 Tag ID [19]" in prompt for prompt in prompts)
    assert not any("Tag 0 标定板" in prompt or "A-01 Tag ID" in prompt for prompt in prompts)


def test_demo_setup_can_defer_c_tags_and_continue_to_station_setup(tmp_path, monkeypatch):
    from wenshi_patrol.demo import run_interactive_setup
    from wenshi_patrol.demo_setup import DEMO_PLANTS, DemoSetupSession

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    root = destination.parent / "setup_20260918_105456"
    image = np.full((24, 32, 3), 127, dtype=np.uint8)
    setup = DemoSetupSession(root)
    setup.set_operator("cjc")
    setup.record_calibration_board(image)
    for plant_id, tag_id, observed in DEMO_PLANTS:
        if observed:
            setup.record_tag(plant_id, tag_id, photo=image, observed=observed)

    class Status:
        def connect(self): return True
        def wait_for_status(self, **_kwargs): return True
        def disconnect(self): pass

    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "q"

    monkeypatch.setattr("wenshi_patrol.demo.AGVStatusClient", lambda *_args, **_kwargs: Status())
    monkeypatch.setattr("builtins.input", answer)

    monkeypatch.setattr("wenshi_patrol.demo._demo_route_rows", lambda _config: (0.097, -2.334))
    result = run_interactive_setup(
        {"agv": {"ip": "192.0.2.5"}},
        destination,
        camera_enabled=False,
        resume_root=root,
        skip_archived_tags=True,
    )

    assert result == 2
    assert any("A-01" in prompt for prompt in prompts)
    assert not any("C-01" in prompt for prompt in prompts)


def test_demo_setup_migrates_legacy_stations_and_resumes_at_left_viewpoint(tmp_path, monkeypatch):
    from wenshi_patrol.demo import run_interactive_setup
    from wenshi_patrol.demo_setup import DEMO_PLANTS, DemoSetupSession

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    root = destination.parent / "setup_20260918_105456"
    image = np.full((24, 32, 3), 127, dtype=np.uint8)
    setup = DemoSetupSession(root)
    setup.set_operator("cjc")
    setup.record_calibration_board(image)
    for plant_id, tag_id, observed in DEMO_PLANTS:
        if observed:
            setup.record_tag(plant_id, tag_id, photo=image, observed=observed)
    for index in range(1, 9):
        setup.record_station(
            f"left-{index:02d}",
            {"x": float(index), "y": -2.20, "angle": 3.12},
            photo=image,
            source="agv_status",
        )
        setup.record_station(
            f"right-{index:02d}",
            {"x": float(index) + 0.25, "y": 0.12, "angle": 0.01},
            photo=image,
            source="agv_status",
        )
    setup.record_viewpoint("home_safe", [0.0] * 6)

    class Arm:
        def connect(self, **_kwargs): return True
        def wait_for_joint_state(self, **_kwargs): return True
        def disconnect(self): pass

    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "q"

    monkeypatch.setattr("wenshi_patrol.demo._demo_route_rows", lambda _config: (0.097, -2.334), raising=False)
    monkeypatch.setattr("wenshi_patrol.demo.AGVStatusClient", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stations must not be rerecorded")))
    monkeypatch.setattr("wenshi_patrol.demo.JakaClient", lambda *_args, **_kwargs: Arm())
    monkeypatch.setattr("builtins.input", answer)

    result = run_interactive_setup(
        {"jaka": {"ip": "192.0.2.160"}},
        destination,
        camera_enabled=False,
        resume_root=root,
        skip_archived_tags=True,
    )

    assert result == 2
    assert prompts and "姿态 left" in prompts[0]
    resumed = DemoSetupSession.resume(root)
    assert len(resumed.stations) == 24
    assert "A-01" in resumed.stations
    assert resumed.stations["B-L-01"]["derived_from"] == "B-R-01"


def test_demo_setup_with_saved_left_resumes_at_right_without_center(tmp_path, monkeypatch):
    from wenshi_patrol.demo import run_interactive_setup
    from test_demo_setup import _complete_session

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    setup = _complete_session(tmp_path)
    setup.viewpoints.pop("right")
    setup.checkpoint()
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "q"

    monkeypatch.setattr("wenshi_patrol.demo._demo_route_rows", lambda _config: (0.097, -2.334))
    monkeypatch.setattr("builtins.input", answer)

    result = run_interactive_setup(
        {"jaka": {"ip": "192.0.2.160"}},
        destination,
        camera_enabled=False,
        resume_root=setup.root,
        skip_archived_tags=True,
    )

    assert result == 2
    assert prompts and "姿态 right" in prompts[0]
    assert not any("center" in prompt for prompt in prompts)


def test_demo_setup_arm_snapshot_uses_a_fresh_short_connection():
    from wenshi_patrol.demo import _setup_arm_pose

    events = []

    class Arm:
        last_error = ""

        def disconnect(self):
            events.append("disconnect")

        def connect(self, **_kwargs):
            events.append("connect")
            return True

        def wait_for_joint_state(self, **_kwargs):
            events.append("wait")
            return True

        def snapshot(self):
            events.append("snapshot")
            return {"joint": [1.0] * 6, "tcp": [2.0] * 6}

    joint, tcp = _setup_arm_pose(Arm(), "right")

    assert joint == [1.0] * 6
    assert tcp == [2.0] * 6
    assert events == ["disconnect", "connect", "wait", "snapshot", "disconnect"]


def test_demo_setup_arm_snapshot_disconnects_after_read_timeout():
    from wenshi_patrol.demo import _setup_arm_pose

    events = []

    class Arm:
        last_error = "timed out"

        def disconnect(self):
            events.append("disconnect")

        def connect(self, **_kwargs):
            events.append("connect")
            return True

        def wait_for_joint_state(self, **_kwargs):
            events.append("wait")
            return False

    with pytest.raises(RuntimeError, match="timed out"):
        _setup_arm_pose(Arm(), "right")

    assert events == ["disconnect", "connect", "wait", "disconnect"]


def test_demo_setup_retries_same_arm_viewpoint_after_transient_read_failure(tmp_path, monkeypatch):
    from wenshi_patrol.demo import run_interactive_setup
    from test_demo_setup import _complete_session

    destination = tmp_path / "runtime" / "demo" / "demo_setup.json"
    setup = _complete_session(tmp_path)
    setup.viewpoints.pop("left")
    setup.viewpoints.pop("right")
    setup.checkpoint()

    class Arm:
        def connect(self, **_kwargs): return True
        def wait_for_joint_state(self, **_kwargs): return True
        def disconnect(self): pass

    attempts = iter((RuntimeError("temporary joint timeout"), ([1.0] * 6, [2.0] * 6)))
    prompts = []

    def read_pose(_arm, name):
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    def answer(prompt):
        prompts.append(prompt)
        return "yes" if len(prompts) < 3 else "q"

    monkeypatch.setattr("wenshi_patrol.demo.JakaClient", lambda *_args, **_kwargs: Arm())
    monkeypatch.setattr("wenshi_patrol.demo._setup_arm_pose", read_pose)
    monkeypatch.setattr("wenshi_patrol.demo._demo_route_rows", lambda _config: (0.097, -2.334), raising=False)
    monkeypatch.setattr("builtins.input", answer)

    result = run_interactive_setup(
        {"jaka": {"ip": "192.0.2.160"}},
        destination,
        camera_enabled=False,
        resume_root=setup.root,
        skip_archived_tags=True,
    )

    assert result == 2
    assert sum("姿态 left" in prompt for prompt in prompts) == 2, prompts
    resumed = __import__("wenshi_patrol.demo_setup", fromlist=["DemoSetupSession"]).DemoSetupSession.resume(setup.root)
    assert resumed.viewpoints["left"]["joint"] == [1.0] * 6


def test_demo_requires_verified_home_safe(tmp_path, monkeypatch):
    from wenshi_patrol.demo import DemoArm

    monkeypatch.setattr("wenshi_patrol.demo.load_viewpoints", lambda _config: {
        "camera": {"joint": [0.0] * 6},
        "camera_left": {"joint": [0.0] * 6},
        "camera_right": {"joint": [0.0] * 6},
    })
    config = {
        "jaka": {
            "ip": "127.0.0.1",
            "port": 10001,
            "left_pose": "camera_left",
            "center_pose": "camera",
            "right_pose": "camera_right",
        }
    }
    with pytest.raises(ValueError, match="home_safe"):
        DemoArm(config)


def test_demo_rejects_nonfinite_viewpoint(tmp_path, monkeypatch):
    from wenshi_patrol.demo import DemoArm

    monkeypatch.setattr("wenshi_patrol.demo.load_viewpoints", lambda _config: {
        "camera": {"joint": [0.0] * 6},
        "camera_left": {"joint": [0.0] * 6},
        "camera_right": {"joint": [0.0] * 6},
        "home_safe": {"joint": [float("nan")] + [0.0] * 5},
    })
    config = {"jaka": {"ip": "127.0.0.1", "port": 10001}}
    with pytest.raises(ValueError, match="home_safe"):
        DemoArm(config)


def test_demo_attaches_to_nearest_forward_segment():
    from wenshi_patrol.control.route_math import make_segments
    from wenshi_patrol.demo import DemoController

    controller = DemoController.__new__(DemoController)
    controller.stations = {
        "LM1": (0.0, 0.0, 0.0),
        "LM4": (1.0, 0.0, 0.0),
        "LM3": (1.0, 1.0, 0.0),
        "LM2": (0.0, 1.0, 0.0),
    }
    controller.order = ["LM1", "LM4", "LM3", "LM2"]
    controller.segments = make_segments(controller.stations, controller.order, loop=True)
    controller.station_snap_m = 0.25
    controller.config = {"safety": {"hard_cross_track_m": 0.25}}
    controller.log = lambda _message: None
    assert controller._route_attachment_index({"x": 1.0, "y": 0.0, "angle": 0.0}) == 1


def test_demo_random_lap_selects_at_most_five_unique_observation_groups():
    from wenshi_patrol.demo import choose_demo_groups
    from wenshi_patrol.demo_setup import OBSERVATION_GROUPS

    groups = list(OBSERVATION_GROUPS)
    selected = choose_demo_groups(groups, min_count=3, max_count=5, rng=__import__("random").Random(7))
    assert 3 <= len(selected) <= 5
    assert len(selected) == len(set(selected))
    assert set(selected).issubset(set(groups))
    assert not {"LM1", "LM2", "LM3", "LM4"}.intersection(selected)


def test_demo_random_pool_contains_all_twenty_four_active_plants():
    from wenshi_patrol.demo_setup import OBSERVATION_GROUPS

    assert len(OBSERVATION_GROUPS) == 24
    assert OBSERVATION_GROUPS[:2] == ("A-01", "A-02")
    assert "B-L-08" in OBSERVATION_GROUPS
    assert "B-R-08" in OBSERVATION_GROUPS
    assert not any(name.startswith("C-") for name in OBSERVATION_GROUPS)


def test_demo_setup_requires_all_twenty_four_observation_targets(tmp_path):
    from wenshi_patrol.demo import load_demo_setup

    path = tmp_path / "setup.json"
    path.write_text(
        '{"kind": "wenshi_expert_demo", "schema_version": 1, "tag_family": "tag25h7", '
        '"tag_size_m": 0.09, "tags": {}, "viewpoints": {"home_safe": {"joint": [0,0,0,0,0,0]}, '
        '"left": {"joint": [0,0,0,0,0,0]}, "center": {"joint": [0,0,0,0,0,0]}, '
        '"right": {"joint": [0,0,0,0,0,0]}}, "stations": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="24"):
        load_demo_setup(path)


def test_demo_setup_loads_real_observation_stations_and_viewpoints(tmp_path):
    from wenshi_patrol.demo import load_demo_setup
    from wenshi_patrol.demo_setup import OBSERVATION_GROUPS
    from test_demo_setup import _complete_session

    session = _complete_session(tmp_path)
    path = tmp_path / "setup.json"
    session.publish(path)
    value = load_demo_setup(path)
    assert list(value["stations"]) == list(OBSERVATION_GROUPS)
    assert value["stations"]["A-01"] == pytest.approx((-0.9, -2.2, 3.14159))
    assert value["viewpoints"]["camera_left"]["joint"] == [1.0] * 6


def test_demo_setup_rejects_manually_typed_station_pose(tmp_path):
    from wenshi_patrol.demo import load_demo_setup
    from test_demo_setup import _complete_session

    path = tmp_path / "setup.json"
    _complete_session(tmp_path).publish(path)
    setup = __import__("json").loads(path.read_text(encoding="utf-8"))
    setup["stations"]["A-01"]["pose_source"] = "manual"
    path.write_text(__import__("json").dumps(setup), encoding="utf-8")
    with pytest.raises(ValueError, match="AGV"):
        load_demo_setup(path)


def test_demo_lap_inserts_only_selected_observation_points_without_diagonals():
    from wenshi_patrol.demo import build_demo_lap_segments

    map_stations = {
        "LM1": (0.0, 0.0, 0.0),
        "LM4": (10.0, 0.0, 0.0),
        "LM3": (10.0, 2.0, 0.0),
        "LM2": (0.0, 2.0, 0.0),
    }
    observations = {
        **{f"left-{index:02d}": (float(index), 0.0, 0.0) for index in range(1, 9)},
        **{f"right-{index:02d}": (float(9 - index), 2.0, 3.14) for index in range(1, 9)},
    }
    selected = ["left-02", "left-07", "right-04"]
    segments = build_demo_lap_segments(
        map_stations,
        ["LM1", "LM4", "LM3", "LM2"],
        observations,
        selected,
        max_distance_m=0.25,
    )
    endpoints = {segment.end_name for segment in segments}
    assert endpoints.intersection(observations) == set(selected)
    assert {"LM1", "LM2", "LM3", "LM4"}.issubset(endpoints)
    assert all(segment.start[0] == segment.end[0] or segment.start[1] == segment.end[1] for segment in segments)


def test_demo_rejects_observation_point_far_from_route():
    from wenshi_patrol.demo import build_demo_lap_segments

    map_stations = {
        "LM1": (0.0, 0.0, 0.0),
        "LM4": (10.0, 0.0, 0.0),
        "LM3": (10.0, 2.0, 0.0),
        "LM2": (0.0, 2.0, 0.0),
    }
    with pytest.raises(ValueError, match="路线"):
        build_demo_lap_segments(
            map_stations,
            ["LM1", "LM4", "LM3", "LM2"],
            {"left-01": (5.0, 5.0, 0.0)},
            ["left-01"],
            max_distance_m=0.25,
        )


def test_demo_does_not_import_height_or_yolo_paths():
    text = Path("app/wenshi_patrol/demo.py").read_text(encoding="utf-8")
    assert "height_test" not in text
    assert "field_test" not in text
    assert "ultralytics" not in text
