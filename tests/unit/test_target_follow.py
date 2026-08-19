from wenshi_patrol.target_follow import TargetFollowController
from wenshi_patrol.vision.detector import Detection


def test_follow_center_has_zero_j5_error():
    controller = TargetFollowController(gain=0.4, max_speed_deg_s=10.0)
    command = controller.update(Detection(320, 100, 80, 100, 0.9, class_name="rice"), 640, 0.1)
    assert command.speed_deg_s == 0.0


def test_follow_clamps_j5_command():
    controller = TargetFollowController(gain=2.0, max_speed_deg_s=10.0)
    command = controller.update(Detection(0, 100, 80, 100, 0.9, class_name="rice"), 640, 0.1)
    assert abs(command.speed_deg_s) <= 10.0
