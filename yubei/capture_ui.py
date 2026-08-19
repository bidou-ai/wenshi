"""Optional OpenCV preview for the command-line dataset collector."""

from __future__ import annotations

import cv2
import numpy as np


def show_rgb(image: np.ndarray, title: str = "yubei dataset RGB") -> bool:
    cv2.imshow(title, image)
    return (cv2.waitKey(1) & 0xFF) != ord("q")


def close_preview() -> None:
    cv2.destroyAllWindows()

