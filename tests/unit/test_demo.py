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
    controller.status = Status()
    controller.motion = Motion()
    controller.arm = Arm()
    controller.photo_dir = Path(tmp_path)
    controller._stopped = False
    controller._running = False
    controller._photo_index = 0
    controller._lock = threading.Lock()
    controller.log = lambda _message: None
    return controller


def test_demo_stop_is_idempotent(tmp_path):
    controller = _controller(tmp_path)
    controller.stop("operator")
    controller.stop("repeat")
    assert controller.motion.stop_calls == 1
    assert controller.arm.stop_calls == 1
    assert controller.arm.safe_calls == 1


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

        def observe(self):
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


def test_demo_fresh_status_rejects_agv_alarm(tmp_path):
    controller = _controller(tmp_path)
    controller.status.value["errors"] = ["drive fault"]
    with pytest.raises(RuntimeError, match="报警"):
        controller._fresh_status()


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


def test_demo_loads_height_setup_viewpoint_aliases(tmp_path):
    from wenshi_patrol.demo import _load_demo_viewpoints

    path = tmp_path / "setup.json"
    path.write_text(
        '{"viewpoints": {"home_safe": {"joint": [0, 0, 0, 0, 0, 0]}, '
        '"left": {"joint": [1, 1, 1, 1, 1, 1]}, '
        '"center": {"joint": [2, 2, 2, 2, 2, 2]}, '
        '"right": {"joint": [3, 3, 3, 3, 3, 3]}}}',
        encoding="utf-8",
    )
    viewpoints = _load_demo_viewpoints(path)
    assert viewpoints["camera_left"]["joint"][0] == 1
    assert viewpoints["camera"]["joint"][0] == 2
    assert viewpoints["camera_right"]["joint"][0] == 3


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

    groups = [f"left-{index:02d}" for index in range(1, 9)] + [f"right-{index:02d}" for index in range(1, 9)]
    selected = choose_demo_groups(groups, min_count=3, max_count=5, rng=__import__("random").Random(7))
    assert 3 <= len(selected) <= 5
    assert len(selected) == len(set(selected))
    assert set(selected).issubset(set(groups))
    assert not {"LM1", "LM2", "LM3", "LM4"}.intersection(selected)


def test_demo_setup_requires_all_sixteen_observation_stations(tmp_path):
    from wenshi_patrol.demo import load_demo_setup

    path = tmp_path / "setup.json"
    path.write_text(
        '{"viewpoints": {"home_safe": {"joint": [0,0,0,0,0,0]}, '
        '"left": {"joint": [0,0,0,0,0,0]}, "center": {"joint": [0,0,0,0,0,0]}, '
        '"right": {"joint": [0,0,0,0,0,0]}}, "stations": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="16"):
        load_demo_setup(path)


def test_demo_setup_loads_real_observation_stations_and_viewpoints(tmp_path):
    from wenshi_patrol.demo import load_demo_setup

    groups = [f"left-{index:02d}" for index in range(1, 9)] + [f"right-{index:02d}" for index in range(1, 9)]
    setup = {
        "viewpoints": {name: {"joint": [float(index)] * 6} for index, name in enumerate(("home_safe", "left", "center", "right"))},
        "stations": {name: {"group_id": name, "pose": {"x": index / 10, "y": 0.0, "angle": 0.0}} for index, name in enumerate(groups)},
    }
    path = tmp_path / "setup.json"
    path.write_text(__import__("json").dumps(setup), encoding="utf-8")
    value = load_demo_setup(path)
    assert list(value["stations"]) == groups
    assert value["stations"]["left-01"] == (0.0, 0.0, 0.0)
    assert value["viewpoints"]["camera_left"]["joint"] == [1.0] * 6


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
    assert "ultralytics" not in text
