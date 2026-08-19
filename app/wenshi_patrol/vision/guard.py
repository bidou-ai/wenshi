"""视觉运行边界：只允许产出检测记录，不允许取得运动权限。"""

from __future__ import annotations

from dataclasses import dataclass


class VisionDisabledError(RuntimeError):
    """识别被配置为关闭。"""


@dataclass(frozen=True)
class VisionPolicy:
    enabled: bool
    motion_enable: bool
    model_path: str

    def __post_init__(self):
        if self.motion_enable:
            raise ValueError("视觉模块不允许拥有运动权限")
        if self.enabled and not self.model_path.strip():
            raise ValueError("视觉识别已启用，但缺少模型路径")

    def require_detection(self):
        if not self.enabled:
            raise VisionDisabledError("视觉识别未启用")

