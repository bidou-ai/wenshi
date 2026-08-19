from pathlib import Path

from wenshi_patrol.arm_controller import ArmSweepWorker
from wenshi_patrol.config import load_config


ROOT = Path(__file__).resolve().parents[2]


def test_disabled_fixed_approach_cannot_start_arm_sequence():
    worker = ArmSweepWorker(load_config(ROOT / "config" / "wenshi.yaml"), lambda _message: None)
    ok, message = worker.start_fixed_sequence("right")
    assert not ok
    assert "未启用" in message

