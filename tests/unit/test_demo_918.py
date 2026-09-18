from pathlib import Path
import random
import threading

import numpy as np
import pytest


def _controller(tmp_path):
    from wenshi_patrol.demo_918 import Controller918

    class Status:
        def __init__(self):
            self.value = {
                "x": 0.0,
                "y": 0.0,
                "angle": 0.0,
                "is_stop": True,
                "status_age": 0.0,
                "blocked": False,
                "emergency": False,
                "fatals": [],
                "errors": [],
                "brake": False,
            }

        def get_status(self):
            return dict(self.value)

    class Motion:
        def __init__(self):
            self.stop_calls = 0
            self.commands = []

        def stop(self):
            self.stop_calls += 1

        def set_velocity(self, vx, angular):
            self.commands.append((vx, angular))

    class Arm:
        def __init__(self):
            self.safe_calls = 0
            self.stop_calls = 0
            self.observed = []

        def move_to_safe(self):
            self.safe_calls += 1
            return True

        def observe(self, view):
            self.observed.append(view)
            return True

        def stop(self):
            self.stop_calls += 1

    controller = Controller918.__new__(Controller918)
    controller.config = {
        "control": {
            "endpoint_tolerance_m": 0.10,
            "cross_track_gain": 0.8,
            "heading_gain": 1.6,
            "max_angular_speed_rad_s": 0.35,
            "correction_threshold_m": 0.04,
            "rotate_in_place_threshold_deg": 30.0,
            "heading_slowdown_threshold_deg": 10.0,
            "min_heading_scale": 0.20,
        },
        "safety": {"hard_cross_track_m": 0.25, "station_stop_timeout_s": 0.2},
        "demo_918": {
            "route_speed_mps": 0.18,
            "endpoint_slowdown_distance_m": 0.60,
            "endpoint_min_speed_mps": 0.04,
            "station_position_tolerance_m": 0.15,
            "heading_alignment_tolerance_deg": 3.0,
            "heading_alignment_timeout_s": 1.0,
            "heading_alignment_gain": 2.0,
            "heading_alignment_max_rad_s": 0.45,
            "heading_alignment_min_rad_s": 0.08,
        },
    }
    controller.status = Status()
    controller.motion = Motion()
    controller.arm = Arm()
    controller.camera_enabled = True
    controller.camera = lambda: np.zeros((8, 8, 3), dtype=np.uint8)
    controller.photo_dir = Path(tmp_path) / "photos"
    controller._photo_index = 0
    controller._stop_event = threading.Event()
    controller._pause_event = threading.Event()
    controller._stopped = False
    controller._running = True
    controller._lock = threading.Lock()
    controller._route_thread = None
    controller.current_station = "P-01"
    controller.observations = {
        "P-01": {"pose": (1.0, 0.0, 0.0), "arm_view": "left"},
    }
    controller.log = lambda _message: None
    controller.ros = None
    return controller


def test_918_shuttle_route_goes_out_and_back_but_observes_once():
    from wenshi_patrol.demo_918 import build_918_lap_segments

    segments = build_918_lap_segments(
        {"R-01": (0.0, 0.0, 0.0), "R-02": (2.0, 0.0, 0.0)},
        ["R-01", "R-02"],
        {"P-01": {"pose": (1.0, 0.0, 0.0), "arm_view": "left"}},
        ["P-01"],
        "shuttle",
        0.25,
    )

    assert [(item.start_name, item.end_name) for item in segments] == [
        ("R-01", "P-01"),
        ("P-01", "R-02"),
        ("R-02", "R-01"),
    ]
    assert sum(item.end_name == "P-01" for item in segments) == 1


def test_918_loop_route_closes_after_inserting_selected_observation():
    from wenshi_patrol.demo_918 import build_918_lap_segments

    segments = build_918_lap_segments(
        {
            "R-01": (0.0, 0.0, 0.0),
            "R-02": (2.0, 0.0, 0.0),
            "R-03": (2.0, 2.0, 0.0),
        },
        ["R-01", "R-02", "R-03"],
        {"P-01": {"pose": (1.0, 0.0, 0.0), "arm_view": "right"}},
        ["P-01"],
        "loop",
        0.25,
    )

    assert [(item.start_name, item.end_name) for item in segments] == [
        ("R-01", "P-01"),
        ("P-01", "R-02"),
        ("R-02", "R-03"),
        ("R-03", "R-01"),
    ]


def test_918_route_rejects_observation_far_from_taught_path():
    from wenshi_patrol.demo_918 import build_918_lap_segments

    with pytest.raises(ValueError, match="偏离.*路线"):
        build_918_lap_segments(
            {"R-01": (0.0, 0.0, 0.0), "R-02": (2.0, 0.0, 0.0)},
            ["R-01", "R-02"],
            {"P-01": {"pose": (1.0, 1.0, 0.0), "arm_view": "left"}},
            ["P-01"],
            "shuttle",
            0.25,
        )


@pytest.mark.parametrize(
    ("count", "minimum", "maximum"),
    ((1, 1, 1), (2, 2, 2), (3, 3, 3), (5, 3, 5), (8, 3, 5)),
)
def test_918_random_selection_scales_with_available_points(count, minimum, maximum):
    from wenshi_patrol.demo_918 import choose_918_observations

    names = [f"P-{index:02d}" for index in range(1, count + 1)]
    selected = choose_918_observations(names, rng=random.Random(9))

    assert minimum <= len(selected) <= maximum
    assert len(selected) == len(set(selected))
    assert set(selected).issubset(names)


def test_918_controller_uses_observation_specific_arm_side(tmp_path):
    controller = _controller(tmp_path)
    controller.observations["P-01"]["arm_view"] = "right"
    controller._stop_agv_and_wait = lambda: None
    controller._align_observation_heading = lambda: None
    controller._require_observation_pose = lambda: None

    controller._observe_current()

    assert controller.arm.observed == ["right"]
    assert controller.arm.safe_calls == 2


def test_918_heading_alignment_rotates_in_place_before_arm(tmp_path, monkeypatch):
    from wenshi_patrol.demo_918 import Controller918

    controller = _controller(tmp_path)
    headings = iter((20.0, 7.0, 2.0, 2.0))
    controller.observations["P-01"]["pose"] = (1.0, 0.0, 0.0)
    controller._fresh_status = lambda: {
        "x": 1.0,
        "y": 0.0,
        "angle": __import__("math").radians(next(headings)),
        "is_stop": False,
    }
    controller._stop_agv_and_wait = lambda: None
    monkeypatch.setattr("wenshi_patrol.demo_918.time.sleep", lambda _seconds: None)

    Controller918._align_observation_heading(controller)

    assert controller.motion.commands
    assert all(vx == 0.0 for vx, _angular in controller.motion.commands)
    assert all(angular < 0.0 for _vx, angular in controller.motion.commands)


def test_918_heading_alignment_never_rotates_after_pause(tmp_path):
    from wenshi_patrol.demo_918 import Controller918, Demo918Paused

    controller = _controller(tmp_path)
    controller._running = False
    controller._pause_event.set()
    controller._fresh_status = lambda: {"x": 1.0, "y": 0.0, "angle": 1.0, "is_stop": False}

    with pytest.raises(Demo918Paused):
        Controller918._align_observation_heading(controller)

    assert controller.motion.commands == []


@pytest.mark.parametrize(
    ("is_stop", "heading_deg", "message"),
    ((False, 0.0, "停稳"), (True, 5.0, "3.0deg")),
)
def test_918_final_arm_gate_requires_stop_and_three_degrees(tmp_path, is_stop, heading_deg, message):
    from wenshi_patrol.demo_918 import Controller918

    controller = _controller(tmp_path)
    controller._fresh_status = lambda: {
        "x": 1.0,
        "y": 0.0,
        "angle": __import__("math").radians(heading_deg),
        "is_stop": is_stop,
    }

    with pytest.raises(RuntimeError, match=message):
        Controller918._require_observation_pose(controller)


def test_918_route_reaches_cruise_speed_then_slows(tmp_path, monkeypatch):
    from wenshi_patrol.control.route_math import Segment
    from wenshi_patrol.demo_918 import Controller918

    controller = _controller(tmp_path)
    statuses = iter((
        {"x": 0.0, "y": 0.0, "angle": 0.0},
        {"x": 0.7, "y": 0.0, "angle": 0.0},
        {"x": 0.95, "y": 0.0, "angle": 0.0},
    ))
    controller._fresh_status = lambda: next(statuses)
    monkeypatch.setattr("wenshi_patrol.demo_918.time.sleep", lambda _seconds: None)

    assert Controller918._run_segment(
        controller,
        Segment("R-01", "R-02", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    )

    assert controller.motion.commands[0] == pytest.approx((0.18, 0.0))
    assert 0.04 < controller.motion.commands[1][0] < 0.18


def test_918_route_never_commands_velocity_after_pause_transition(tmp_path, monkeypatch):
    from wenshi_patrol.control.route_math import Segment
    from wenshi_patrol.demo_918 import Controller918

    controller = _controller(tmp_path)

    def pause_before_command():
        with controller._lock:
            controller._running = False
            controller._pause_event.set()
        return {"x": 0.0, "y": 0.0, "angle": 0.0, "is_stop": True}

    controller._fresh_status = pause_before_command
    monkeypatch.setattr("wenshi_patrol.demo_918.time.sleep", lambda _seconds: controller._stop_event.set())

    assert Controller918._run_segment(
        controller,
        Segment("R-01", "R-02", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    ) is False
    assert controller.motion.commands == []


def test_918_arm_uses_50_observe_60_retract_and_80_accel():
    from wenshi_patrol.demo_918 import Arm918

    calls = []

    class Client:
        def joint_move(self, target, speed, accel, timeout, **_kwargs):
            calls.append((target, speed, accel, timeout))
            return True

    arm = Arm918.__new__(Arm918)
    arm.client = Client()
    arm.poses = {"left": [1.0] * 6, "right": [2.0] * 6}
    arm.safe = [0.0] * 6
    arm.observe_speed = 50.0
    arm.retract_speed = 60.0
    arm.accel = 80.0
    arm.timeout = 120.0
    arm.observation_hold_s = 0.0
    arm._motion_lock = threading.Lock()
    arm._cancel_event = threading.Event()
    arm.cancel_requested = None

    assert arm.observe("left")
    assert arm.move_to_safe()
    assert calls[0][1:3] == (50.0, 80.0)
    assert calls[1][1:3] == (60.0, 80.0)


def test_918_photo_writes_only_to_918_photo_directory(tmp_path):
    from wenshi_patrol.demo_918 import Controller918

    controller = _controller(tmp_path)
    output = Controller918.save_photo(controller)

    assert output.parent == (tmp_path / "photos").resolve()
    assert output.is_file()


def test_918_rviz_selection_rejects_unknown_observation():
    from wenshi_patrol.demo_918 import RosPublisher918

    publisher = RosPublisher918.__new__(RosPublisher918)
    publisher._selection_lock = threading.Lock()
    publisher._observation_names = {"P-01", "P-02"}
    publisher._selected = set()

    publisher.update_selection(["P-02"])
    assert publisher._selected == {"P-02"}

    with pytest.raises(ValueError, match="不存在"):
        publisher.update_selection(["P-03"])


def test_918_setup_station_pose_requires_agv_stopped():
    from wenshi_patrol.demo_918 import _setup_station_pose

    class Status:
        def wait_for_status(self, **_kwargs):
            return True

        def get_status(self):
            return {"x": 1.0, "y": 2.0, "angle": 0.0, "is_stop": False}

    with pytest.raises(RuntimeError, match="停稳"):
        _setup_station_pose(Status())


def test_918_interactive_setup_builds_no_tag_site_from_dynamic_points(tmp_path, monkeypatch):
    from test_demo_918_setup import _write_map
    from wenshi_patrol.demo_918 import run_interactive_setup
    from wenshi_patrol.demo_918_setup import load_918_setup

    runtime_root = tmp_path / "runtime" / "9.18"
    destination = runtime_root / "demo_setup.json"
    old_setup = tmp_path / "runtime" / "demo" / "demo_setup.json"
    old_setup.parent.mkdir(parents=True)
    old_setup.write_text(
        __import__("json").dumps({
            "viewpoints": {
                "home_safe": {"joint": [0.0] * 6},
                "left": {"joint": [1.0] * 6},
                "right": {"joint": [2.0] * 6},
            }
        }),
        encoding="utf-8",
    )
    positions = iter((
        (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0), (2.0, 0.0, 0.0),
        (1.0, 0.05, 0.0), (1.0, 0.05, 0.0),
    ))

    class Status:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self):
            return True

        def wait_for_status(self, **_kwargs):
            return True

        def get_status(self):
            x, y, angle = next(positions)
            return {
                "x": x, "y": y, "angle": angle, "is_stop": True,
                "blocked": False, "emergency": False, "fatals": [], "errors": [], "brake": False,
            }

        def disconnect(self):
            pass

    answers = iter((
        "cjc", "shuttle", "2", "", "", "1", "", "left", "",
    ))
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr("wenshi_patrol.demo_918.AGVStatusClient", Status)
    monkeypatch.setattr(
        "wenshi_patrol.demo_918._setup_photo",
        lambda _camera, _label: np.zeros((8, 8, 3), dtype=np.uint8),
    )
    config = {
        "agv": {"ip": "127.0.0.1"},
        "jaka": {"ip": "127.0.0.1"},
        "camera": {"server_url": "http://127.0.0.1:5000"},
        "demo_918": {"setup_position_stability_m": 0.02, "setup_heading_stability_deg": 2.0},
    }

    result = run_interactive_setup(
        config,
        destination,
        runtime_root,
        map_source=_write_map(tmp_path / "new.smap"),
        resume_root=None,
        camera_enabled=False,
        reuse_viewpoints_file=old_setup,
    )

    assert result == 0
    value = load_918_setup(destination)
    assert value["route_mode"] == "shuttle"
    assert list(value["anchors"]) == ["R-01", "R-02"]
    assert list(value["observations"]) == ["P-01"]
    assert value["observations"]["P-01"]["arm_view"] == "left"
    assert "tags" not in value["raw"]
