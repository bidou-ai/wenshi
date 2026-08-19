"""Standalone HTTP client for the Windows D435 service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import time
import urllib.request
from typing import Any

import cv2
import numpy as np


@dataclass
class CameraFrame:
    color: np.ndarray
    depth: np.ndarray
    seq: int
    intrinsics: dict[str, Any]
    received_at: float


class HttpCameraClient:
    def __init__(self, base_url: str, timeout_s: float = 2.0, opener=None):
        self.base_url = str(base_url).rstrip("/")
        self.timeout_s = max(float(timeout_s), 0.1)
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get_json(self, suffix: str) -> dict[str, Any]:
        with self.opener.open(f"{self.base_url}/{suffix.lstrip('/')}", timeout=self.timeout_s) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"camera response is not an object: {suffix}")
        return value

    def health(self) -> dict[str, Any]:
        return self._get_json("health")

    @staticmethod
    def _decode(value: str, flags: int, label: str) -> np.ndarray:
        try:
            raw = base64.b64decode(str(value).encode("ascii"), validate=True)
        except Exception as exc:
            raise RuntimeError(f"{label} base64 decode failed: {exc}") from exc
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), flags)
        if image is None:
            raise RuntimeError(f"{label} image decode failed")
        return image

    def frame(self) -> CameraFrame:
        packet = self._get_json("frame")
        if not packet.get("ok"):
            raise RuntimeError(str(packet.get("error", "camera frame is not ready")))
        color = self._decode(packet.get("color_jpeg_b64", ""), cv2.IMREAD_COLOR, "color")
        depth = self._decode(packet.get("depth_png_b64", ""), cv2.IMREAD_UNCHANGED, "depth")
        if depth.dtype != np.uint16:
            depth = depth.astype(np.uint16)
        try:
            seq = int(packet["seq"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("camera packet has no valid seq") from exc
        intrinsics = packet.get("intrinsics")
        if not isinstance(intrinsics, dict):
            raise RuntimeError("camera packet has no intrinsics object")
        return CameraFrame(color, depth, seq, intrinsics, time.monotonic())

