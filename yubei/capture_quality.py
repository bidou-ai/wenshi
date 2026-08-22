"""Small, dependency-light quality and duplicate checks for RGB capture."""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def assess_image(image: np.ndarray) -> dict[str, Any]:
    if not isinstance(image, np.ndarray) or image.size == 0 or image.ndim < 2:
        return {"ok": False, "reasons": ["empty_image"], "metrics": {}}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    low, high = np.percentile(gray, [2, 98])
    contrast = float(high - low)
    mean_luma = float(gray.mean())
    reasons: list[str] = []
    if sharpness < 35.0:
        reasons.append("blur")
    if contrast < 20.0:
        reasons.append("low_contrast")
    if mean_luma < 25.0:
        reasons.append("underexposed")
    elif mean_luma > 235.0:
        reasons.append("overexposed")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "metrics": {
            "sharpness": round(sharpness, 3),
            "contrast": round(contrast, 3),
            "mean_luma": round(mean_luma, 3),
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
        },
    }


def image_signature(image: np.ndarray) -> int:
    """Return a compact dHash-like signature for nearby duplicate detection."""
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("cannot hash an empty image")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = resized[:, 1:] >= resized[:, :-1]
    value = 0
    for bit in bits.reshape(-1):
        value = (value << 1) | int(bool(bit))
    return value


def signature_distance(first: int, second: int) -> int:
    return int((int(first) ^ int(second)).bit_count())
