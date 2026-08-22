"""In-memory near-photo burst selection."""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from .vision.detector import Detection
from .vision.quality import QualityResult, score_frame
from .vision.targeting import DepthSummary


@dataclass(frozen=True)
class NearFrameResult:
    image: np.ndarray
    detection: Detection
    quality: QualityResult


def accept_near_frame(
    last_stamp_s: float | None,
    frame_stamp_s: float,
    frame_age_s: float,
    max_age_s: float,
) -> bool:
    stamp = float(frame_stamp_s)
    age = float(frame_age_s)
    if not math.isfinite(stamp) or not math.isfinite(age) or age < 0.0:
        return False
    if age > max(float(max_age_s), 0.0):
        return False
    return last_stamp_s is None or stamp > float(last_stamp_s) + 1e-9


def near_burst_action(
    quality_ok: bool,
    completed_rounds: int,
    max_rounds: int,
) -> str:
    if quality_ok:
        return "save"
    if int(completed_rounds) + 1 < max(1, int(max_rounds)):
        return "retry_hold"
    return "fail"


def choose_best_frame(frames: list[tuple[np.ndarray, Detection, DepthSummary | None]], rounds: int = 3, burst_count: int = 5) -> NearFrameResult:
    if not frames:
        raise ValueError("near burst contains no frames")
    candidates = frames[: max(1, int(rounds)) * max(1, int(burst_count))]
    evaluated = [NearFrameResult(image, detection, score_frame(image, detection, depth, expected_upper_body=True)) for image, detection, depth in candidates]
    return max(evaluated, key=lambda result: (result.quality.ok, result.quality.score, result.quality.metrics.get("sharpness", 0.0)))
