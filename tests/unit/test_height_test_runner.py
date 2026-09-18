import numpy as np
import threading
import yaml


def _parts():
    from wenshi_patrol.height_test.models import HeightTestConfig, FramePacket, DetectionBundle
    from wenshi_patrol.vision.detector import Detection
    config = HeightTestConfig.from_project(yaml.safe_load(open("config/wenshi.yaml")))
    class Source:
        def __init__(self): self.seq = 0
        def capture_burst(self, count):
            self.seq += 1
            return [FramePacket(np.zeros((20, 20, 3), np.uint8), np.full((20, 20), 1000, np.uint16), self.seq, {"fx": 20, "fy": 20, "cx": 10, "cy": 10})]
    class Models:
        def infer(self, image):
            return DetectionBundle(
                "p",
                "q",
                (Detection(5, 10, 8, 12, .9), Detection(15, 10, 8, 16, .85)),
                (Detection(5, 5, 3, 3, .8), Detection(15, 4, 3, 3, .75)),
                image,
            )
    class Arm:
        def __init__(self): self.moves = []; self.stop_calls = 0
        def move_to_view(self, view): self.moves.append(view); return True
        def move_to_safe(self): self.moves.append("safe")
        def stop(self): self.stop_calls += 1
    class Agv:
        def __init__(self): self.status = {"is_stop": True, "x": 0.0, "y": 0.0, "angle": 0.0}; self.stop_calls = 0
        def stop(self): self.stop_calls += 1
    class Setup:
        stations = {key: {"pose": {"x": 0.0, "y": 0.0, "angle": 0.0}} for key in config.groups}
        tags = {}
        water_offsets = {key: .1 for key in config.active_plant_ids}
    return config, Source(), Models(), Arm(), Agv(), Setup()


def test_arm_only_refuses_moving_agv(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore
    config, source, models, arm, agv, setup = _parts()
    agv.status["is_stop"] = False
    outcome = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup).run_arm_only("left-01")
    assert outcome.ok is False
    assert arm.moves == []


def test_arm_only_refuses_agv_stopped_at_the_wrong_station(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore

    config, source, models, arm, agv, setup = _parts()
    agv.status.update({"x": 0.30, "y": 0.0, "angle": 0.0})
    outcome = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup).run_arm_only("left-01")
    assert outcome.ok is False
    assert outcome.errors == ("AGV is not parked at left-01",)
    assert arm.moves == []


def test_arm_only_refuses_wrong_agv_heading_at_station(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore

    config, source, models, arm, agv, setup = _parts()
    agv.status["angle"] = 0.25
    outcome = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup).run_arm_only("left-01")
    assert outcome.ok is False
    assert outcome.errors == ("AGV is not parked at left-01",)
    assert arm.moves == []


def test_two_plant_group_captures_each_view_once_and_assigns_distinct_boxes(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore

    config, source, models, arm, agv, setup = _parts()
    outcome = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup).run_arm_only("left-01")
    assert arm.moves == ["left", "center", "right", "safe"]
    assert source.seq == 3
    assert outcome.results["A-01"].methods["bbox_pixel_height"].value_m == 12.0
    assert outcome.results["B-L-01"].methods["bbox_pixel_height"].value_m == 16.0
    assert all(view.metadata["panicle_count"] == 1 for result in outcome.results.values() for view in result.views)


def test_two_plant_group_does_not_assign_one_detection_to_both_plants(tmp_path):
    from wenshi_patrol.height_test.models import DetectionBundle
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore
    from wenshi_patrol.vision.detector import Detection

    config, source, models, arm, agv, setup = _parts()
    models.infer = lambda image: DetectionBundle("p", "q", (Detection(5, 10, 8, 12, .9),), (), image)
    outcome = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup).run_arm_only("left-01")
    assert outcome.ok is False
    assert outcome.results["A-01"].candidate_value_m is None
    assert outcome.results["B-L-01"].candidate_value_m is None


def test_right_group_assigns_single_left_side_active_plant_without_requiring_c_row(tmp_path):
    from wenshi_patrol.height_test.models import DetectionBundle
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore
    from wenshi_patrol.vision.detector import Detection

    config, source, models, arm, agv, setup = _parts()
    models.infer = lambda image: DetectionBundle(
        "p",
        "q",
        (Detection(5, 10, 8, 12, .9),),
        (Detection(5, 5, 3, 3, .8),),
        image,
    )
    outcome = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup).run_arm_only("right-01")
    assert set(outcome.results) == {"B-R-01"}
    assert outcome.results["B-R-01"].candidate_value_m is not None


def test_stop_is_idempotent(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore
    config, source, models, arm, agv, setup = _parts()
    runner = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup)
    runner.stop("operator"); runner.stop("repeat")
    assert arm.stop_calls == 1 and agv.stop_calls == 1
    assert arm.moves == ["safe"]


def test_capture_failure_stops_and_attempts_safe_retract(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore

    config, source, models, arm, agv, setup = _parts()
    source.capture_burst = lambda _count: (_ for _ in ()).throw(RuntimeError("camera failed"))
    runner = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup)
    outcome = runner.run_arm_only("left-01")
    assert outcome.ok is False
    assert arm.stop_calls == 1
    assert arm.moves[-1] == "safe"


def test_stop_prevents_later_arm_view_motion(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore

    config, source, models, arm, agv, setup = _parts()
    runner = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup)
    runner.stop("operator")
    try:
        runner._move_arm("left")
    except RuntimeError as exc:
        assert "stopped" in str(exc)
    else:
        raise AssertionError("arm moved after stop")


def test_stop_cancels_view_motion_between_runner_check_and_client_send(tmp_path):
    from wenshi_patrol.height_test.cli import _ArmAdapter
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore

    config, source, models, _arm, agv, setup = _parts()
    entered = threading.Event()
    release = threading.Event()

    class Client:
        def __init__(self):
            self.commands = []

        def joint_move(self, pose, _speed, _accel, _timeout, *, cancel_requested=None):
            if pose == [9.0] * 6:
                self.commands.append("safe")
                return True
            entered.set()
            assert release.wait(timeout=1.0)
            if cancel_requested is not None and cancel_requested():
                return False
            self.commands.append("view")
            return True

        def stop(self):
            self.commands.append("stop")

    arm = _ArmAdapter.__new__(_ArmAdapter)
    arm.client = Client()
    arm.poses = {"camera_left": [0.0] * 6, "camera": [0.0] * 6, "camera_right": [0.0] * 6}
    arm.safe = [9.0] * 6
    arm.speed = arm.accel = arm.timeout = 1.0
    arm._cancel_requested = threading.Event()
    runner = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup)
    errors = []

    def move_view():
        try:
            runner._move_arm("left")
        except RuntimeError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=move_view)
    worker.start()
    assert entered.wait(timeout=1.0)
    runner.stop("operator")
    release.set()
    worker.join(timeout=1.0)
    assert "view" not in arm.client.commands
    assert errors == ["arm failed to move to left"]
