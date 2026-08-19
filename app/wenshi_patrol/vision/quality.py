"""Image quality heuristics for far and near patrol photos."""

from __future__ import annotations

from dataclasses import dataclass
import cv2
import numpy as np

from .detector import Detection
from .targeting import DepthSummary


@dataclass(frozen=True)
class QualityResult:
    ok: bool
    score: float
    reasons: list[str]
    metrics: dict[str, float]


def score_frame(image: np.ndarray, detection: Detection, depth: DepthSummary | None, expected_upper_body: bool = True, min_sharpness: float = 35.0, min_area_ratio: float = 0.03) -> QualityResult:
    reasons: list[str] = []
    if image is None or image.size == 0 or image.ndim < 2:
        return QualityResult(False, 0.0, ["图片为空"], {})
    height, width = image.shape[:2]
    x1 = float(detection.cx) - float(detection.width) / 2.0
    y1 = float(detection.cy) - float(detection.height) / 2.0
    x2 = float(detection.cx) + float(detection.width) / 2.0
    y2 = float(detection.cy) + float(detection.height) / 2.0
    margin = max(5.0, min(width, height) * 0.02)
    if x1 < margin or y1 < margin or x2 > width - margin or y2 > height - margin:
        reasons.append("目标靠近图片边缘或被裁切")
    area_ratio = max(0.0, min(1.0, detection.area / float(width * height)))
    if area_ratio < float(min_area_ratio):
        reasons.append("目标过小")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharpness < float(min_sharpness):
        reasons.append("画面模糊")
    low, high = np.percentile(gray, [2, 98])
    contrast = max(float(high - low), float(gray.max()) - float(gray.min()))
    if contrast < 20:
        reasons.append("对比度不足")
    if float(gray.mean()) < 25 or float(gray.mean()) > 235:
        reasons.append("曝光异常")
    if depth is not None:
        if not depth.valid:
            reasons.append("深度无效")
        elif depth.mad_m is not None and depth.mad_m > 0.08:
            reasons.append("深度不稳定")
    score = max(0.0, 1.0 - 0.15 * len(reasons))
    metrics = {"area_ratio": area_ratio, "sharpness": sharpness, "mean_luma": float(gray.mean()), "contrast": contrast}
    return QualityResult(not reasons, score, reasons, metrics)
