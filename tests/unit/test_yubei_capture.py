import io
import json
from pathlib import Path

import cv2
import numpy as np

import yubei.dataset_capture as dataset_capture
from yubei.dataset_capture import CaptureSession, capture_image
from yubei.dataset_capture import camera_url_from_config
from yubei.http_camera import CameraFrame
from yubei.paths import SessionPaths


class FakeCamera:
    def __init__(self):
        self.calls = 0

    def frame(self):
        self.calls += 1
        return CameraFrame(
            color=np.full((8, 10, 3), self.calls, dtype=np.uint8),
            depth=np.zeros((4, 5), dtype=np.uint16),
            seq=self.calls,
            intrinsics={},
            received_at=0.0,
        )


def test_capture_session_enter_saves_exactly_one_jpg(tmp_path: Path):
    paths = SessionPaths.create(tmp_path)
    camera = FakeCamera()
    session = CaptureSession(camera, paths)
    count = session.run(io.StringIO("\nq\n"), io.StringIO(), preview=False)
    assert count == 1
    assert camera.calls == 1
    images = list(paths.images_dir.glob("*.jpg"))
    assert len(images) == 1
    assert cv2.imread(str(images[0])).shape == (8, 10, 3)
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["images"][0]["status"] == "captured"


def test_capture_session_q_before_enter_saves_nothing(tmp_path: Path):
    paths = SessionPaths.create(tmp_path)
    session = CaptureSession(FakeCamera(), paths)
    assert session.run(io.StringIO("q\n"), io.StringIO(), preview=False) == 0
    assert not list(paths.images_dir.glob("*.jpg"))


def test_capture_image_rejects_empty_image(tmp_path: Path):
    try:
        capture_image(np.empty((0, 0, 3), dtype=np.uint8), tmp_path / "x.jpg")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty image was accepted")


def test_live_preview_enter_saves_the_frame_that_was_displayed(tmp_path, monkeypatch):
    paths = SessionPaths.create(tmp_path)
    camera = FakeCamera()
    monkeypatch.setattr(dataset_capture, "show_rgb", lambda _image, **_kwargs: True)
    monkeypatch.setattr(dataset_capture, "close_preview", lambda: None)
    session = CaptureSession(camera, paths)

    saved = session.run(io.StringIO("\nq\n"), io.StringIO(), preview=True)

    image = cv2.imread(str(next(paths.images_dir.glob("*.jpg"))))
    assert saved == 1
    assert int(image[0, 0, 0]) == 1


def test_capture_session_records_flower_tag_and_camera_frame(tmp_path: Path):
    paths = SessionPaths.create(tmp_path)
    session = CaptureSession(FakeCamera(), paths, capture_tag="flower")

    assert session.run(io.StringIO("\nq\n"), io.StringIO(), preview=False) == 1

    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    item = manifest["images"][0]
    assert item["capture_tag"] == "flower"
    assert item["seq"] == 1
    assert "quality" in item


def test_capture_session_can_switch_tag_without_capturing(tmp_path: Path):
    paths = SessionPaths.create(tmp_path)
    session = CaptureSession(FakeCamera(), paths)

    assert session.run(io.StringIO("f\n\nq\n"), io.StringIO(), preview=False) == 1

    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["images"][0]["capture_tag"] == "flower"


def test_capture_uses_camera_url_from_formal_config(tmp_path: Path):
    config = tmp_path / "wenshi.yaml"
    config.write_text("camera:\n  server_url: http://10.8.0.203:18080\n", encoding="utf-8")

    assert camera_url_from_config(config) == "http://10.8.0.203:18080"
