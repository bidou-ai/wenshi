from pathlib import Path

from wenshi_patrol.arm_controller import ArmSweepWorker
from wenshi_patrol.config import load_config
from wenshi_patrol.target_follow import TargetFollowController
from wenshi_patrol.vision.detector import Detection


ROOT = Path(__file__).resolve().parents[2]


def test_follow_center_has_zero_j5_error():
    controller = TargetFollowController(gain=0.4, max_speed_deg_s=10.0)
    command = controller.update(Detection(320, 100, 80, 100, 0.9, class_name="rice"), 640, 0.1)
    assert command.speed_deg_s == 0.0


def test_follow_clamps_j5_command():
    controller = TargetFollowController(gain=2.0, max_speed_deg_s=10.0)
    command = controller.update(Detection(0, 100, 80, 100, 0.9, class_name="rice"), 640, 0.1)
    assert abs(command.speed_deg_s) <= 10.0


def test_arm_worker_uses_vision_target_follow_tuning():
    config = load_config(ROOT / "config" / "wenshi.yaml")
    config["vision"].update({
        "target_follow_gain": 0.42,
        "target_follow_max_speed_deg_s": 3.0,
        "target_follow_deadband_ratio": 0.07,
    })
    worker = ArmSweepWorker(config, lambda _message: None)
    controller = worker._follow_controller
    assert controller.gain == 0.42
    assert controller.max_speed_deg_s == 3.0
    assert controller.deadband_ratio == 0.07
