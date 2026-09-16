"""RGB-D frame acquisition and the two-model inference boundary."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable, Sequence
import urllib.request

import cv2
import numpy as np

from ..vision.detector import Detection, RiceMarkerDetector
from .models import DetectionBundle, FramePacket


class _HttpCameraClient:
    """Minimal local client so the formal app does not import training code."""

    def __init__(self, base_url: str, timeout_s: float):
        self.base_url = str(base_url).rstrip("/")
        self.timeout_s = max(float(timeout_s), 0.1)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _json(self, suffix: str) -> dict[str, Any]:
        with self.opener.open(f"{self.base_url}/{suffix.lstrip('/')}", timeout=self.timeout_s) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("camera response is not an object")
        return value

    @staticmethod
    def _decode(value: str, flags: int, label: str) -> np.ndarray:
        try:
            raw = base64.b64decode(str(value).encode("ascii"), validate=True)
        except Exception as exc:
            raise RuntimeError(f"{label} base64 decode failed") from exc
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), flags)
        if image is None:
            raise RuntimeError(f"{label} image decode failed")
        return image

    def frame(self) -> Any:
        packet = self._json("frame")
        if not packet.get("ok"):
            raise RuntimeError(str(packet.get("error", "camera frame is not ready")))
        class _Frame: pass
        frame = _Frame()
        frame.color = self._decode(packet.get("color_jpeg_b64", ""), cv2.IMREAD_COLOR, "color")
        frame.depth = self._decode(packet.get("depth_png_b64", ""), cv2.IMREAD_UNCHANGED, "depth")
        if frame.depth.dtype != np.uint16:
            frame.depth = frame.depth.astype(np.uint16)
        frame.seq = int(packet["seq"])
        frame.intrinsics = packet.get("intrinsics") or {}
        frame.stamp = packet.get("stamp")
        frame.profile = packet.get("profile") if isinstance(packet.get("profile"), dict) else None
        frame.received_at = time.monotonic()
        return frame


class HttpRgbdSource:
    """Fetch aligned RGB-D packets and reject stale or malformed responses."""

    def __init__(self, url: str, timeout_s: float = 2.0, client: Any | None = None):
        if client is None:
            client = _HttpCameraClient(url, timeout_s=timeout_s)
        self.client = client
        self.url = str(url)
        self.timeout_s = float(timeout_s)
        self._last_seq: int | None = None

    def capture(self) -> FramePacket:
        frame = self.client.frame()
        getter = frame.get if isinstance(frame, dict) else lambda key, default=None: getattr(frame, key, default)
        color = getter("color")
        depth = getter("depth")
        seq = int(getter("seq"))
        if self._last_seq is not None and seq <= self._last_seq:
            raise RuntimeError(f"camera seq is not increasing: {seq} <= {self._last_seq}")
        if not isinstance(color, np.ndarray) or not isinstance(depth, np.ndarray):
            raise RuntimeError("camera returned invalid RGB-D arrays")
        if color.shape[:2] != depth.shape[:2]:
            raise RuntimeError("camera RGB-D dimensions do not match")
        self._last_seq = seq
        return FramePacket(
            color=color,
            depth=depth,
            seq=seq,
            intrinsics=dict(getter("intrinsics", {}) or {}),
            stamp=getter("stamp"),
            profile=getter("profile"),
            received_at=float(getter("received_at", time.monotonic())),
        )

    def capture_burst(self, count: int = 3) -> list[FramePacket]:
        if int(count) < 1:
            raise ValueError("burst count must be positive")
        frames: list[FramePacket] = []
        for _ in range(int(count)):
            frames.append(self.capture())
        return frames


def _sharpness(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image[..., :3], cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()) if gray.size else 0.0


def _depth_ratio(depth: np.ndarray) -> float:
    values = np.asarray(depth).reshape(-1)
    return float(np.count_nonzero(np.isfinite(values) & (values > 0)) / max(values.size, 1))


def select_best_frame(frames: Sequence[FramePacket], quality: dict[str, Any] | None = None) -> FramePacket:
    if not frames:
        raise ValueError("camera returned an empty burst")
    threshold = float((quality or {}).get("min_depth_valid_ratio", 0.70))
    valid = [frame for frame in frames if _depth_ratio(frame.depth) >= threshold]
    if not valid:
        raise RuntimeError("no burst frame meets the minimum depth validity")
    return max(valid, key=lambda frame: (_sharpness(frame.color), _depth_ratio(frame.depth), frame.seq))


class ModelSuite:
    """Keep plant and panicle model outputs separate and auditable."""

    def __init__(self, plant_model: str | Path, panicle_model: str | Path, conf_plant: float = 0.25, conf_panicle: float = 0.20, loader: Callable[[Path], Any] | None = None):
        self.plant_model = str(plant_model)
        self.panicle_model = str(panicle_model)
        self.conf_plant = float(conf_plant)
        self.conf_panicle = float(conf_panicle)
        self._loader = loader
        self._plant = None
        self._panicle = None
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if self._loader is not None:
            self._plant = self._loader(Path(self.plant_model))
            self._panicle = self._loader(Path(self.panicle_model))
        else:
            self._plant = RiceMarkerDetector(self.plant_model, self.conf_plant, allow_missing_model=False)
            self._panicle = RiceMarkerDetector(self.panicle_model, self.conf_panicle, allow_missing_model=False)
            if not self._plant.load_model() or not self._panicle.load_model():
                raise RuntimeError("failed to load plant or panicle model")
        self._loaded = True

    @staticmethod
    def _infer(model: Any, image: np.ndarray, conf: float) -> list[Detection]:
        if hasattr(model, "detect"):
            return list(model.detect(image))
        result = model(image, conf=conf, verbose=False)
        detector = RiceMarkerDetector(allow_missing_model=True)
        detector.model = model
        return detector.detect(image)

    def infer(self, color: np.ndarray) -> DetectionBundle:
        if not isinstance(color, np.ndarray) or color.size == 0:
            raise ValueError("color image is empty")
        self._load()
        plant = tuple(self._infer(self._plant, color, self.conf_plant))
        panicle = tuple(self._infer(self._panicle, color, self.conf_panicle))
        overlay = color.copy()
        if hasattr(self._plant, "draw"):
            overlay = self._plant.draw(overlay, list(plant))
        if hasattr(self._panicle, "draw"):
            overlay = self._panicle.draw(overlay, list(panicle))
        return DetectionBundle(self.plant_model, self.panicle_model, plant, panicle, overlay, {"plant_count": len(plant), "panicle_count": len(panicle)})


__all__ = ["DetectionBundle", "HttpRgbdSource", "ModelSuite", "select_best_frame"]
