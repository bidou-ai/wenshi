"""Bounded J5 target-follow controller."""

from __future__ import annotations

from dataclasses import dataclass

from .vision.detector import Detection


@dataclass(frozen=True)
class J5Command:
    speed_deg_s: float
    error_ratio: float


class TargetFollowController:
    def __init__(self, gain: float = 0.8, max_speed_deg_s: float = 10.0, deadband_ratio: float = 0.03):
        self.gain = float(gain)
        self.max_speed_deg_s = abs(float(max_speed_deg_s))
        self.deadband_ratio = abs(float(deadband_ratio))

    def update(self, detection: Detection, image_width: int, dt_s: float) -> J5Command:
        del dt_s
        error_ratio = (float(detection.cx) - float(image_width) / 2.0) / max(float(image_width) / 2.0, 1.0)
        if abs(error_ratio) <= self.deadband_ratio:
            return J5Command(0.0, error_ratio)
        speed = max(-self.max_speed_deg_s, min(self.max_speed_deg_s, self.gain * error_ratio * self.max_speed_deg_s))
        return J5Command(speed, error_ratio)
