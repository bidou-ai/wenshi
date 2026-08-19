import pytest

from wenshi_patrol.vision.guard import VisionDisabledError, VisionPolicy


def test_disabled_vision_cannot_run_detection():
    policy = VisionPolicy(enabled=False, motion_enable=False, model_path="")
    with pytest.raises(VisionDisabledError, match="视觉识别未启用"):
        policy.require_detection()


def test_vision_policy_rejects_any_motion_permission():
    with pytest.raises(ValueError, match="运动权限"):
        VisionPolicy(enabled=True, motion_enable=True, model_path="model.onnx")


def test_enabled_vision_requires_model_path():
    with pytest.raises(ValueError, match="模型路径"):
        VisionPolicy(enabled=True, motion_enable=False, model_path="")

