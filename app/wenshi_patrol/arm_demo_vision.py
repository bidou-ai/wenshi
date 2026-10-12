"""Latest-frame vision pipeline for the arm panicle demonstration.

The worker threads in this module only acquire/decode images and run inference.  UI
composition is deliberately a pure image operation so the OpenCV GUI remains owned by
the foreground demo loop.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

import cv2
import numpy as np

from .vision.detector import Detection, RiceMarkerDetector


def _immutable_image(image: np.ndarray) -> np.ndarray:
    """Snapshot an image into an immutable bytes buffer, not merely a readonly flag."""
    contiguous = np.ascontiguousarray(image)
    return np.frombuffer(contiguous.tobytes(), dtype=contiguous.dtype).reshape(contiguous.shape)


@dataclass(frozen=True)
class FrozenDetection:
    """Immutable result snapshot with the public geometry API of ``Detection``."""

    cx: float
    cy: float
    width: float
    height: float
    confidence: float
    class_id: int | None = None
    class_name: str = "target"

    @classmethod
    def from_detection(cls, detection: Detection) -> "FrozenDetection":
        return cls(
            cx=float(detection.cx),
            cy=float(detection.cy),
            width=float(detection.width),
            height=float(detection.height),
            confidence=float(detection.confidence),
            class_id=detection.class_id,
            class_name=str(detection.class_name),
        )

    @property
    def area(self) -> float:
        return self.width * self.height

    def pixel_at(self, x_ratio: float, y_ratio: float) -> tuple[float, float]:
        x1 = self.cx - self.width / 2.0
        y1 = self.cy - self.height / 2.0
        return (
            x1 + self.width * max(0.0, min(float(x_ratio), 1.0)),
            y1 + self.height * max(0.0, min(float(y_ratio), 1.0)),
        )

    def to_dict(self) -> dict:
        return {
            "cx": self.cx,
            "cy": self.cy,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "area": self.area,
        }


@dataclass(frozen=True)
class RgbFrame:
    seq: int
    image: np.ndarray
    captured_at: float

    def __post_init__(self):
        object.__setattr__(self, "image", _immutable_image(self.image))


@dataclass(frozen=True)
class VisionResult:
    seq: int
    raw: np.ndarray
    annotated: np.ndarray
    detections: tuple[FrozenDetection, ...]
    inference_ms: float
    completed_at: float

    def __post_init__(self):
        for field in ("raw", "annotated"):
            object.__setattr__(self, field, _immutable_image(getattr(self, field)))
        object.__setattr__(
            self,
            "detections",
            tuple(FrozenDetection.from_detection(detection) for detection in self.detections),
        )


class LatestSlot:
    """A condition-backed, capacity-one slot which always drops older values."""

    def __init__(self):
        self._condition = threading.Condition()
        self._value: Any = None
        self._generation = 0

    def put(self, value: Any) -> None:
        with self._condition:
            self._generation += 1
            self._value = value
            self._condition.notify_all()

    def get(self) -> Any:
        with self._condition:
            return self._value

    def wait_for_new(
        self, after_generation: int | None, timeout: float | None
    ) -> tuple[int, Any] | None:
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        after = -1 if after_generation is None else int(after_generation)
        with self._condition:
            while self._value is None or self._generation <= after:
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(remaining)
            return self._generation, self._value


class SnapshotCamera:
    """Read RGB from the same ``/frame`` endpoint used by the field test."""

    def __init__(self, base_url: str, *, timeout_s: float = 1.0, opener: Any | None = None):
        self.base_url = str(base_url).rstrip("/")
        self.timeout_s = float(timeout_s)
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._last_health: dict[str, Any] | None = None
        self._fallback_seq = 0

    def health(self) -> dict[str, Any]:
        with self._opener.open(f"{self.base_url}/health", timeout=self.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("相机 health 响应不是 JSON 对象")
        self._last_health = payload
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("error", "相机未就绪")))
        if "seq" in payload:
            try:
                if int(payload["seq"]) < 0:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError("相机 health seq 无效")
        return payload

    def capture(self) -> RgbFrame:
        with self._opener.open(f"{self.base_url}/frame", timeout=self.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("相机 frame 响应不是 JSON 对象")
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("error", "相机未就绪")))
        try:
            seq = int(payload["seq"])
            if seq < 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("相机 frame seq 无效") from exc
        try:
            encoded = base64.b64decode(
                str(payload.get("color_jpeg_b64", "")).encode("ascii"),
                validate=True,
            )
        except Exception as exc:
            raise RuntimeError(f"相机 frame JPEG base64 解码失败: {exc}") from exc
        if not encoded:
            raise RuntimeError("相机 frame JPEG 为空")
        image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("相机 frame JPEG 解码失败")
        self._fallback_seq = max(self._fallback_seq, seq)
        return RgbFrame(seq=seq, image=np.ascontiguousarray(image), captured_at=time.monotonic())


class PanicleVisionPipeline:
    """Capture and inference workers with latest-frame semantics."""

    def __init__(
        self,
        camera: SnapshotCamera,
        model_path: str = "",
        *,
        conf_threshold: float = 0.25,
        poll_interval_s: float = 0.05,
        detector_factory: Callable[..., Any] | None = None,
    ):
        self.camera = camera
        self.model_path = model_path
        self.conf_threshold = float(conf_threshold)
        self.poll_interval_s = max(0.001, float(poll_interval_s))
        self._detector_factory = detector_factory or RiceMarkerDetector
        self._frames = LatestSlot()
        self._results = LatestSlot()
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._started = False
        self._threads: list[threading.Thread] = []
        self._detector: Any | None = None
        self._detector_lock = threading.Lock()
        self._fatal_error: str | None = None
        self._last_health_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_result_at: float | None = None
        self._health_message: str | None = None

    @property
    def fatal_error(self) -> str | None:
        return self._fatal_error

    @property
    def detector(self) -> Any | None:
        return self._detector

    def _prepare_detector(self) -> None:
        if self._detector is not None:
            return
        with self._detector_lock:
            if self._detector is not None:
                return
            detector = self._detector_factory(
                model_path=self.model_path,
                conf_threshold=self.conf_threshold,
                target_class_names=["panicle"],
                allow_missing_model=False,
            )
            loaded = detector.load_model()
            if loaded is False:
                raise RuntimeError("稻穗识别模型加载失败")
            self._detector = detector

    def start(self) -> "PanicleVisionPipeline":
        with self._lifecycle_lock:
            if self._fatal_error is not None:
                raise RuntimeError(f"视觉流水线不可启动: {self._fatal_error}")
            if self._started:
                return self
            alive = [thread.name for thread in self._threads if thread.is_alive()]
            if alive:
                raise RuntimeError(f"视觉 worker 未退出，不可启动: {', '.join(alive)}")
            self._prepare_detector()
            # A restarted run must not expose or infer on data from its previous round.
            self._frames = LatestSlot()
            self._results = LatestSlot()
            self._last_health_at = None
            self._last_frame_at = None
            self._last_result_at = None
            self._health_message = None
            stop_event = threading.Event()
            self._stop = stop_event
            self._started = True
            self._threads = [
                threading.Thread(
                    target=self._capture_loop,
                    args=(stop_event,),
                    name="arm-demo-capture",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._inference_loop,
                    args=(stop_event,),
                    name="arm-demo-inference",
                    daemon=True,
                ),
            ]
            for thread in self._threads:
                thread.start()
        return self

    def _capture_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                frame = self.camera.capture()
                self._last_health_at = frame.captured_at
                self._last_frame_at = frame.captured_at
                self._health_message = None
                self._frames.put(frame)
            except Exception as exc:
                self._health_message = str(exc)
            stop_event.wait(self.poll_interval_s)

    def _inference_loop(self, stop_event: threading.Event) -> None:
        last_generation = 0
        failures = 0
        while not stop_event.is_set():
            published = self._frames.wait_for_new(
                last_generation, timeout=self.poll_interval_s
            )
            if published is None:
                continue
            last_generation, frame = published
            try:
                # Own separate copies: detector/draw implementations may mutate their input.
                raw = np.ascontiguousarray(frame.image.copy())
                inference_input = raw.copy()
                started = time.monotonic()
                detections = list(self._detector.detect(inference_input))
                annotated = raw.copy()
                drawn = self._detector.draw(annotated, detections)
                if drawn is not None:
                    annotated = np.ascontiguousarray(drawn)
                result = VisionResult(
                    seq=int(frame.seq),
                    raw=np.ascontiguousarray(raw),
                    annotated=np.ascontiguousarray(annotated),
                    detections=tuple(detections),
                    inference_ms=(time.monotonic() - started) * 1000.0,
                    completed_at=time.monotonic(),
                )
                self._results.put(result)
                self._last_result_at = result.completed_at
                failures = 0
            except Exception as exc:
                failures += 1
                if failures >= 3:
                    self._fatal_error = f"连续 3 次稻穗推理失败: {exc}"
                    stop_event.set()

    def latest(self) -> VisionResult | None:
        return self._results.get()

    def health_error(self, max_age_s: float = 1.0) -> str | None:
        if self._fatal_error:
            return self._fatal_error
        now = time.monotonic()
        limit = max(0.0, float(max_age_s))
        for label, timestamp in (("相机", self._last_frame_at), ("检测结果", self._last_result_at)):
            if timestamp is None:
                if label == "相机" and self._health_message:
                    return self._health_message
                return f"{label}尚未更新"
            if now - timestamp > limit:
                return self._health_message or f"{label}已过期"
        return None

    def stop(self) -> None:
        with self._lifecycle_lock:
            stop_event = self._stop
            threads = tuple(self._threads)
        stop_event.set()
        for thread in threads:
            thread.join(timeout=1.0)
        alive = [thread.name for thread in threads if thread.is_alive()]
        with self._lifecycle_lock:
            if alive:
                self._fatal_error = f"视觉 worker 未退出: {', '.join(alive)}"
                return
            self._threads = []
            self._started = False


def _letterbox(image: np.ndarray, size: tuple[int, int], color: tuple[int, int, int]) -> np.ndarray:
    width, height = size
    source = np.asarray(image)
    if source.ndim != 3 or source.shape[2] != 3:
        raise ValueError("图像必须是 HxWx3 BGR")
    scale = min(width / source.shape[1], height / source.shape[0])
    resized = cv2.resize(source, (max(1, round(source.shape[1] * scale)), max(1, round(source.shape[0] * scale))))
    output = np.full((height, width, 3), color, dtype=np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    output[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return output


def compose_dual_view(
    result: VisionResult,
    state_text: str,
    canvas_size: tuple[int, int] = (1600, 900),
) -> np.ndarray:
    """Compose fixed-size raw/annotated views without changing either source image."""

    width, height = int(canvas_size[0]), int(canvas_size[1])
    if width < 2 or height < 1:
        raise ValueError("canvas_size 太小")
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    panel_width = width // 2
    left = _letterbox(result.raw.copy(), (panel_width, height), (24, 28, 32))
    right = _letterbox(result.annotated.copy(), (width - panel_width, height), (24, 28, 32))
    canvas[:, :panel_width] = left
    canvas[:, panel_width:] = right
    cv2.putText(canvas, "LIVE RGB", (20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (235, 235, 235), 2, cv2.LINE_AA)
    # ASCII keeps OpenCV's built-in Hershey font legible on every workstation.
    cv2.putText(canvas, "PANICLE DETECTION", (panel_width + 20, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (235, 235, 235), 2, cv2.LINE_AA)
    cv2.putText(canvas, f"CANDIDATES {len(result.detections)}  {result.inference_ms:.1f} ms", (panel_width + 20, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(canvas, str(state_text), (20, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 240), 1, cv2.LINE_AA)
    return canvas
