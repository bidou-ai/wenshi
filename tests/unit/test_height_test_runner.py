import numpy as np
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
        def infer(self, image): return DetectionBundle("p", "q", (Detection(10, 10, 10, 15, .9),), (Detection(10, 4, 4, 4, .8),), image)
    class Arm:
        def __init__(self): self.moves = []; self.stop_calls = 0
        def move_to_view(self, view): self.moves.append(view); return True
        def move_to_safe(self): self.moves.append("safe")
        def stop(self): self.stop_calls += 1
    class Agv:
        def __init__(self): self.status = {"is_stop": True}; self.stop_calls = 0
        def stop(self): self.stop_calls += 1
    class Setup:
        stations = {key: {} for key in config.groups}
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


def test_stop_is_idempotent(tmp_path):
    from wenshi_patrol.height_test.runner import HeightTestRunner
    from wenshi_patrol.height_test.storage import HeightTestStore
    config, source, models, arm, agv, setup = _parts()
    runner = HeightTestRunner(config, source, models, HeightTestStore.create(tmp_path), arm, agv, setup)
    runner.stop("operator"); runner.stop("repeat")
    assert arm.stop_calls == 1 and agv.stop_calls == 1
