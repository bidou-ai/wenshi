"""手眼标定矩阵的离线加载边界。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def load_transform(path: str | Path) -> np.ndarray:
    with Path(path).expanduser().open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    matrix = value.get("matrix") if isinstance(value, dict) else value
    flat = [float(item) for item in matrix]
    if len(flat) != 16:
        raise ValueError("手眼变换必须包含 16 个数值")
    result = np.asarray(flat, dtype=np.float64).reshape(4, 4)
    if not np.all(np.isfinite(result)):
        raise ValueError("手眼变换包含非有限数值")
    return result

