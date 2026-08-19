import base64
import json
from contextlib import contextmanager

import cv2
import numpy as np

from yubei.http_camera import HttpCameraClient


def _encoded(image: np.ndarray, ext: str) -> str:
    ok, buffer = cv2.imencode(ext, image)
    assert ok
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _packet() -> dict:
    color = np.zeros((720, 1280, 3), dtype=np.uint8)
    color[:, :, 1] = 120
    depth = np.full((480, 640), 900, dtype=np.uint16)
    return {
        "ok": True,
        "seq": 7,
        "color_jpeg_b64": _encoded(color, ".jpg"),
        "depth_png_b64": _encoded(depth, ".png"),
        "intrinsics": {"width": 1280, "height": 720, "fx": 600, "fy": 600, "cx": 640, "cy": 360},
    }


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class _Opener:
    def __init__(self, packet):
        self.packet = packet

    def open(self, url, timeout):
        assert timeout == 1.0
        if url.endswith("/health"):
            return _Response({"ok": True, "frames": 9})
        return _Response(self.packet)


def test_camera_client_decodes_color_and_depth():
    client = HttpCameraClient("http://camera.test:18080", opener=_Opener(_packet()), timeout_s=1.0)
    frame = client.frame()
    assert frame.color.shape == (720, 1280, 3)
    assert frame.depth.shape == (480, 640)
    assert frame.depth.dtype == np.uint16
    assert frame.seq == 7
    assert frame.intrinsics["fx"] == 600


def test_camera_client_reports_health():
    client = HttpCameraClient("http://camera.test:18080", opener=_Opener(_packet()), timeout_s=1.0)
    assert client.health()["ok"] is True


def test_camera_client_rejects_bad_packet():
    packet = _packet()
    packet["color_jpeg_b64"] = "not-an-image"
    client = HttpCameraClient("http://camera.test:18080", opener=_Opener(packet), timeout_s=1.0)
    try:
        client.frame()
    except RuntimeError as exc:
        assert "decode" in str(exc).lower()
    else:
        raise AssertionError("bad frame was accepted")
