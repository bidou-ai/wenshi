from pathlib import Path

from wenshi_patrol.arm_controller import ArmSweepWorker
from wenshi_patrol.config import load_config


ROOT = Path(__file__).resolve().parents[2]


class FakeJakaClient:
    def __init__(self, joint, success=False):
        self.connected = True
        self.joint = list(joint)
        self.last_error = ""
        self.moves = []
        self.success = success
        self.on_move = None

    def connect(self, timeout):
        return True

    def wait_for_joint_state(self, timeout):
        return True

    def snapshot(self):
        return {"connected": self.connected, "joint": list(self.joint), "tcp": None,
                "status_age": 0.0, "last_control_response": None, "error": self.last_error}

    def joint_move(self, target, speed, accel, timeout):
        self.moves.append(list(target))
        if self.on_move:
            self.on_move()
        if self.success:
            self.joint = list(target)
            return True
        return False

    def stop(self):
        pass


def make_worker():
    worker = ArmSweepWorker(load_config(ROOT / "config" / "wenshi.yaml"), lambda _message: None)
    return worker


def test_quick_check_rejects_non_j5_patrol_pose():
    worker = make_worker()
    joint = list(worker.center)
    joint[1] += worker.startup_pose_tolerance + 1.0
    worker.client = FakeJakaClient(joint)
    ok, message = worker.quick_start_check()
    assert not ok
    assert "J2偏差" in message


def test_sweep_starts_at_nearest_j5_endpoint_without_center_move():
    worker = make_worker()
    joint = list(worker.center)
    joint[4] = worker.left[4] - 2.0
    worker.client = FakeJakaClient(joint)
    worker.client.on_move = worker._stop.set
    worker._run()
    assert worker.client.moves == [worker.left]


def test_fixed_right_sequence_returns_to_entry_after_photo():
    worker = make_worker()
    entry = list(worker.right)
    pre = [value + 1.0 for value in entry]
    photo = [value + 2.0 for value in entry]
    worker.fixed_poses.update({"camera_right": entry, "right_pre": pre, "right_photo": photo})
    worker.photo_hold_s = 0.0
    client = FakeJakaClient(worker.center, success=True)
    worker.client = client
    worker._run_fixed_sequence("right", "")
    assert client.moves == [entry, pre, photo, pre, entry]
    assert worker.snapshot()["sequence_completed"]
    assert worker.snapshot()["sequence_phase"] == "DONE"


def test_home_executes_only_planned_taught_targets():
    worker = make_worker()
    pre = [value + 2.0 for value in worker.center]
    entry = [value + 1.0 for value in worker.center]
    client = FakeJakaClient([value + 3.0 for value in worker.center], success=True)
    worker.client = client
    worker._run_home([("left_pre", pre), ("camera_left", entry), ("camera", worker.center)])
    assert client.moves == [pre, entry, worker.center]
    assert worker.snapshot()["sequence_phase"] == "HOME_DONE"
