from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import FrozenInstanceError

import cv2
import numpy as np
import pytest

from wenshi_patrol.arm_demo_vision import (
    LatestSlot,
    PanicleVisionPipeline,
    RgbFrame,
    SnapshotCamera,
    VisionResult,
    compose_dual_view,
)
from wenshi_patrol.vision.detector import Detection


def test_latest_slot_keeps_only_newest_value():
    slot = LatestSlot()
    slot.put("old")
    slot.put("new")

    assert slot.get() == "new"
    generation, value = slot.wait_for_new(after_generation=0, timeout=0.01)
    assert (generation, value) == (2, "new")


def test_latest_slot_uses_returned_publication_token_not_value_seq():
    slot = LatestSlot()
    first = RgbFrame(9, np.full((2, 2, 3), 1, dtype=np.uint8), 1.0)
    repeated = RgbFrame(9, np.full((2, 2, 3), 2, dtype=np.uint8), 2.0)
    restarted = RgbFrame(1, np.full((2, 2, 3), 3, dtype=np.uint8), 3.0)

    slot.put(first)
    first_token, first_value = slot.wait_for_new(after_generation=0, timeout=0.01)
    assert first_value is first
    slot.put(repeated)
    repeated_token, repeated_value = slot.wait_for_new(after_generation=first_token, timeout=0.01)
    assert repeated_value is repeated
    slot.put(restarted)
    restarted_token, restarted_value = slot.wait_for_new(
        after_generation=repeated_token, timeout=0.01
    )
    assert restarted_value is restarted
    assert (first_token, repeated_token, restarted_token) == (1, 2, 3)


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class _Opener:
    def __init__(self, image: np.ndarray):
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        self.urls: list[str] = []
        self.responses = {
            "/health": _Response(json.dumps({"ok": True, "seq": 12}).encode()),
            "/frame": _Response(
                json.dumps(
                    {
                        "ok": True,
                        "seq": 12,
                        "color_jpeg_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    }
                ).encode()
            ),
        }

    def open(self, url, timeout):
        self.urls.append(url)
        return self.responses[next(path for path in self.responses if url.endswith(path))]


class _PayloadOpener:
    def __init__(self, health_payload: bytes = b'{"ok": true}', jpeg_payload: bytes = b""):
        self.health_payload = health_payload
        self.jpeg_payload = jpeg_payload

    def open(self, url, timeout):
        if url.endswith("/health"):
            return _Response(self.health_payload)
        if url.endswith("/frame"):
            return _Response(
                json.dumps(
                    {
                        "ok": True,
                        "seq": 1,
                        "color_jpeg_b64": base64.b64encode(self.jpeg_payload).decode("ascii"),
                    }
                ).encode()
            )
        raise AssertionError(url)


class _FrameOpener:
    def __init__(self, image: np.ndarray):
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        self.urls: list[str] = []
        self.payload = json.dumps(
            {
                "ok": True,
                "seq": 23,
                "color_jpeg_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            }
        ).encode()

    def open(self, url, timeout):
        self.urls.append(url)
        return _Response(self.payload)


def test_snapshot_camera_uses_the_field_test_frame_endpoint_once():
    opener = _FrameOpener(np.full((8, 10, 3), 127, dtype=np.uint8))
    camera = SnapshotCamera("http://camera.test:18080", opener=opener)

    frame = camera.capture()

    assert frame.seq == 23
    assert frame.image.shape == (8, 10, 3)
    assert opener.urls == ["http://camera.test:18080/frame"]


def test_snapshot_camera_decodes_jpeg_and_health_seq():
    opener = _Opener(np.full((8, 10, 3), 127, dtype=np.uint8))
    camera = SnapshotCamera("http://camera.test:18080", opener=opener)

    health = camera.health()
    frame = camera.capture()

    assert health["seq"] == 12
    assert frame.seq == 12
    assert frame.image.shape == (8, 10, 3)
    assert opener.urls == [
        "http://camera.test:18080/health",
        "http://camera.test:18080/frame",
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"[]", "JSON 对象"),
        (b'{"ok": false, "error": "HTTP 503"}', "HTTP 503"),
        (b'{"ok": true, "seq": -1}', "seq 无效"),
        (b'{"ok": true, "seq": "bad"}', "seq 无效"),
    ],
)
def test_snapshot_camera_rejects_invalid_health(payload, message):
    camera = SnapshotCamera("http://camera.test", opener=_PayloadOpener(health_payload=payload))

    with pytest.raises(RuntimeError, match=message):
        camera.health()


@pytest.mark.parametrize(
    ("payload", "message"),
    [(b"", "为空"), (b"not a jpeg", "解码失败")],
)
def test_snapshot_camera_rejects_invalid_jpeg(payload, message):
    camera = SnapshotCamera("http://camera.test", opener=_PayloadOpener(jpeg_payload=payload))

    with pytest.raises(RuntimeError, match=message):
        camera.capture()


class _Camera:
    def __init__(self):
        self.seq = 0

    def health(self):
        raise AssertionError("pipeline must not poll the separate health endpoint")

    def capture(self):
        self.seq += 1
        image = np.zeros((40, 60, 3), dtype=np.uint8)
        image[:, :, 0] = self.seq % 255
        return RgbFrame(self.seq, image, time.monotonic())


class _RepeatedSeqCamera(_Camera):
    def capture(self):
        self.seq += 1
        image = np.full((40, 60, 3), self.seq, dtype=np.uint8)
        return RgbFrame(4, image, time.monotonic())


class _FlakyCamera(_Camera):
    def __init__(self):
        super().__init__()
        self.fail = False

    def capture(self):
        if self.fail:
            raise RuntimeError("HTTP 503")
        return super().capture()


class _OfflineCamera:
    def capture(self):
        raise RuntimeError("HTTP 503")


class _RestartCamera(_Camera):
    def __init__(self):
        super().__init__()
        self.block_capture = False
        self.capture_entered = threading.Event()
        self.release_capture = threading.Event()

    def capture(self):
        if self.block_capture:
            self.capture_entered.set()
            self.release_capture.wait()
        return super().capture()


class _Detector:
    instances = 0
    warmups = 0

    def __init__(self, **kwargs):
        type(self).instances += 1
        self.kwargs = kwargs
        self.calls = 0

    def load_model(self):
        return True

    def detect(self, image):
        self.calls += 1
        if int(image.max()) == 0:
            type(self).warmups += 1
            return []
        return [Detection(20, 20, 12, 10, 0.9, class_name="panicle")]

    def draw(self, image, detections):
        cv2.rectangle(image, (10, 10), (30, 30), (0, 255, 0), 2)
        return image


def test_pipeline_loads_model_once_without_synthetic_warmup_and_keeps_same_frame_raw(monkeypatch):
    _Detector.instances = 0
    _Detector.warmups = 0
    pipeline = PanicleVisionPipeline(_Camera(), model_path="panicle.pt", poll_interval_s=0.005, detector_factory=_Detector)
    pipeline.start()
    deadline = time.monotonic() + 1.0
    while pipeline.latest() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    result = pipeline.latest()
    pipeline.stop()

    assert result is not None
    assert _Detector.instances == 1
    assert _Detector.warmups == 0
    assert result.raw.shape == result.annotated.shape
    assert not np.array_equal(result.raw, result.annotated)
    assert np.count_nonzero(result.raw[:, :, 1]) == 0
    assert result.seq == int(result.raw[0, 0, 0])


def test_pipeline_processes_new_publications_when_camera_seq_repeats():
    pipeline = PanicleVisionPipeline(
        _RepeatedSeqCamera(), poll_interval_s=0.001, detector_factory=_Detector
    )
    pipeline.start()
    deadline = time.monotonic() + 1.0
    while (pipeline.detector is None or pipeline.detector.calls < 4) and time.monotonic() < deadline:
        time.sleep(0.005)
    detector = pipeline.detector
    pipeline.stop()

    assert detector is not None
    assert detector.calls >= 4


class _FailingDetector(_Detector):
    def detect(self, image):
        self.calls += 1
        if int(image.max()) == 0:
            return []
        raise RuntimeError("boom")


class _LoadFailureDetector(_Detector):
    def load_model(self):
        return False


class _MutatingDetector(_Detector):
    def detect(self, image):
        self.calls += 1
        if int(image.max()) == 0:
            return []
        image[:] = 255
        return []

    def draw(self, image, detections):
        return image


class _BlockingDetector(_Detector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0

    def detect(self, image):
        self.calls += 1
        if int(image.max()) == 0:
            return []
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        try:
            self.release.wait()
            return []
        finally:
            self.active -= 1


class _BurstCamera:
    def __init__(self):
        self.seq = 0
        self.allow_burst = threading.Event()
        self.burst_done = threading.Event()
        self.finish = threading.Event()

    def health(self):
        return {"ok": True, "seq": self.seq}

    def capture(self):
        if self.seq == 1:
            self.allow_burst.wait()
        if self.seq == 6:
            self.burst_done.set()
            self.finish.wait()
        self.seq += 1
        return RgbFrame(
            self.seq,
            np.full((12, 12, 3), self.seq, dtype=np.uint8),
            time.monotonic(),
        )


class _SlowDetector(_Detector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.processed: list[int] = []

    def detect(self, image):
        self.calls += 1
        value = int(image[0, 0, 0])
        if value == 0:
            return []
        self.processed.append(value)
        if len(self.processed) == 1:
            self.entered.set()
            self.release.wait()
        return []


def test_pipeline_preserves_raw_when_detector_mutates_inference_input():
    pipeline = PanicleVisionPipeline(_Camera(), poll_interval_s=0.002, detector_factory=_MutatingDetector)
    pipeline.start()
    deadline = time.monotonic() + 1.0
    while pipeline.latest() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    result = pipeline.latest()
    pipeline.stop()

    assert result is not None
    assert np.count_nonzero(result.raw) > 0
    assert np.array_equal(result.annotated, result.raw)


def test_pipeline_marks_three_consecutive_inference_errors_fatal():
    pipeline = PanicleVisionPipeline(_Camera(), poll_interval_s=0.001, detector_factory=_FailingDetector)
    pipeline.start()
    deadline = time.monotonic() + 1.0
    while pipeline.fatal_error is None and time.monotonic() < deadline:
        time.sleep(0.005)
    pipeline.stop()
    assert pipeline.fatal_error is not None
    assert "3" in pipeline.fatal_error
    assert pipeline.health_error() == pipeline.fatal_error


def test_pipeline_health_error_reports_initial_camera_failure():
    pipeline = PanicleVisionPipeline(
        _OfflineCamera(), poll_interval_s=0.001, detector_factory=_Detector
    )
    assert pipeline.health_error() == "相机尚未更新"
    pipeline.start()
    try:
        deadline = time.monotonic() + 1.0
        while pipeline._health_message is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert pipeline.health_error() == "HTTP 503"
    finally:
        pipeline.stop()


def test_pipeline_health_error_tolerates_brief_http_error_then_reports_stale():
    camera = _FlakyCamera()
    pipeline = PanicleVisionPipeline(camera, poll_interval_s=0.001, detector_factory=_Detector)
    pipeline.start()
    try:
        deadline = time.monotonic() + 1.0
        while pipeline.latest() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert pipeline.latest() is not None
        camera.fail = True
        deadline = time.monotonic() + 1.0
        while pipeline._health_message is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert pipeline.health_error(max_age_s=1.0) is None
        time.sleep(0.02)
        assert pipeline.health_error(max_age_s=0.005) == "HTTP 503"
    finally:
        pipeline.stop()


def test_pipeline_health_error_reports_stale_camera_without_http_error():
    pipeline = PanicleVisionPipeline(_Camera(), poll_interval_s=0.001, detector_factory=_Detector)
    pipeline.start()
    deadline = time.monotonic() + 1.0
    while pipeline.latest() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pipeline.latest() is not None
    pipeline.stop()

    time.sleep(0.005)
    assert pipeline.health_error(max_age_s=0.001) == "相机已过期"


def test_pipeline_start_propagates_model_load_error():
    pipeline = PanicleVisionPipeline(_Camera(), detector_factory=_LoadFailureDetector)

    with pytest.raises(RuntimeError, match="模型加载失败"):
        pipeline.start()

    assert pipeline._threads == []


def test_pipeline_workers_never_call_opencv_gui(monkeypatch):
    def reject_gui(*_args, **_kwargs):
        raise AssertionError("worker called OpenCV GUI")

    for name in ("imshow", "waitKey", "namedWindow", "destroyWindow", "destroyAllWindows"):
        monkeypatch.setattr(cv2, name, reject_gui)

    pipeline = PanicleVisionPipeline(_Camera(), poll_interval_s=0.001, detector_factory=_Detector)
    pipeline.start()
    try:
        deadline = time.monotonic() + 1.0
        while pipeline.latest() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert pipeline.latest() is not None
        assert pipeline.fatal_error is None
    finally:
        pipeline.stop()


def test_pipeline_normal_stop_joins_workers_and_restart_uses_new_event():
    pipeline = PanicleVisionPipeline(_Camera(), poll_interval_s=0.001, detector_factory=_Detector)
    pipeline.start()
    first_stop_event = pipeline._stop
    first_threads = tuple(pipeline._threads)
    pipeline.stop()

    assert all(not thread.is_alive() for thread in first_threads)
    pipeline.start()
    try:
        assert pipeline._stop is not first_stop_event
    finally:
        pipeline.stop()


def test_pipeline_restart_replaces_slots_and_discards_prior_round_state():
    camera = _RestartCamera()
    pipeline = PanicleVisionPipeline(camera, poll_interval_s=0.001, detector_factory=_Detector)
    pipeline.start()
    pipeline.stop()
    old_frames = pipeline._frames
    old_results = pipeline._results
    old_results.put(
        VisionResult(99, np.ones((2, 2, 3), dtype=np.uint8), np.ones((2, 2, 3), dtype=np.uint8), (), 1.0, 1.0)
    )
    pipeline._last_health_at = 1.0
    pipeline._last_frame_at = 1.0
    pipeline._last_result_at = 1.0
    pipeline._health_message = "prior round error"
    camera.block_capture = True

    pipeline.start()
    try:
        assert camera.capture_entered.wait(1.0)
        assert pipeline._frames is not old_frames
        assert pipeline._results is not old_results
        assert pipeline.latest() is None
        assert pipeline._last_health_at is None
        assert pipeline._last_frame_at is None
        assert pipeline._last_result_at is None
        assert pipeline._health_message is None
    finally:
        camera.release_capture.set()
        pipeline.stop()


def test_pipeline_stop_timeout_retains_worker_and_rejects_restart():
    pipeline = PanicleVisionPipeline(
        _Camera(), poll_interval_s=0.001, detector_factory=_BlockingDetector
    )
    pipeline.start()
    detector = pipeline.detector
    assert detector is not None
    assert detector.entered.wait(1.0)

    pipeline.stop()
    retained_threads = tuple(pipeline._threads)
    try:
        assert retained_threads
        assert any(thread.is_alive() for thread in retained_threads)
        with pytest.raises(RuntimeError, match="不可启动|未退出"):
            pipeline.start()
        assert detector.max_active == 1
    finally:
        detector.release.set()
        pipeline.stop()

    assert all(not thread.is_alive() for thread in retained_threads)
    with pytest.raises(RuntimeError, match="不可启动|未退出"):
        pipeline.start()


def test_pipeline_capacity_one_skips_intermediate_frames_for_slow_detector():
    camera = _BurstCamera()
    pipeline = PanicleVisionPipeline(camera, poll_interval_s=0.001, detector_factory=_SlowDetector)
    pipeline.start()
    detector = pipeline.detector
    assert detector is not None
    try:
        assert detector.entered.wait(1.0)
        camera.allow_burst.set()
        assert camera.burst_done.wait(1.0)
        detector.release.set()
        deadline = time.monotonic() + 1.0
        while len(detector.processed) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert detector.processed[:2] == [1, 6]
    finally:
        detector.release.set()
        camera.finish.set()
        pipeline.stop()


def test_vision_result_snapshots_mutable_detection_input():
    detection = Detection(15, 10, 10, 16, 0.8, class_name="panicle")
    supplied = [detection]
    result = VisionResult(
        seq=1,
        raw=np.zeros((4, 4, 3), dtype=np.uint8),
        annotated=np.zeros((4, 4, 3), dtype=np.uint8),
        detections=supplied,
        inference_ms=2.5,
        completed_at=3.0,
    )

    detection.confidence = 0.1
    supplied.clear()

    assert isinstance(result.detections, tuple)
    assert len(result.detections) == 1
    assert result.detections[0].confidence == 0.8


def test_vision_result_detection_snapshots_are_immutable():
    result = VisionResult(
        seq=1,
        raw=np.zeros((4, 4, 3), dtype=np.uint8),
        annotated=np.zeros((4, 4, 3), dtype=np.uint8),
        detections=(Detection(15, 10, 10, 16, 0.8, class_name="panicle"),),
        inference_ms=2.5,
        completed_at=3.0,
    )

    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.detections[0].confidence = 0.1


def test_frame_and_result_images_use_irrevocably_readonly_backing():
    source = np.full((3, 4, 3), 7, dtype=np.uint8)
    frame = RgbFrame(1, source, 1.0)
    result = VisionResult(1, source, source, (), 1.0, 1.0)

    source[:] = 99
    assert np.all(frame.image == 7)
    assert np.all(result.raw == 7)
    for image in (frame.image, result.raw, result.annotated):
        with pytest.raises(ValueError):
            image.setflags(write=True)


def test_compose_dual_view_is_fixed_letterboxed_and_does_not_draw_on_raw():
    raw = np.full((20, 40, 3), (30, 40, 50), dtype=np.uint8)
    raw[:, :20, 0] = 90
    annotated = raw.copy()
    cv2.rectangle(annotated, (10, 2), (20, 18), (0, 255, 0), 2)
    result = VisionResult(
        seq=1,
        raw=raw,
        annotated=annotated,
        detections=(Detection(15, 10, 10, 16, 0.8, class_name="panicle"),),
        inference_ms=2.5,
        completed_at=time.monotonic(),
    )
    before = raw.copy()
    canvas = compose_dual_view(result, "READY", canvas_size=(200, 200))

    assert canvas.shape == (200, 200, 3)
    assert np.array_equal(raw, before)
    expected_raw = cv2.resize(raw, (100, 50))
    expected_annotated = cv2.resize(annotated, (100, 50))
    left_source = canvas[75:125, :100]
    right_source = canvas[75:125, 100:200]

    assert np.array_equal(left_source, expected_raw)
    assert np.array_equal(right_source, expected_annotated)
    assert not np.any(np.all(left_source == (0, 255, 0), axis=2))
    assert np.any(np.all(right_source == (0, 255, 0), axis=2))
    assert np.all(canvas[140, :100] == (24, 28, 32))
    assert np.all(canvas[140, 100:200] == (24, 28, 32))
