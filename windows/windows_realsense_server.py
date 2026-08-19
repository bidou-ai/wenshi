"""
Windows RealSense HTTP camera server.

Run this on the robot Windows computer that has the USB RealSense connected:

    pip install flask opencv-python numpy pyrealsense2
    python windows_realsense_server.py --host 0.0.0.0 --port 18080

The Ubuntu workstation can then consume:
    http://192.168.192.2:18080/frame
    http://192.168.192.2:18080/stream.mjpg
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
from dataclasses import dataclass, asdict

import cv2
import numpy as np
import pyrealsense2 as rs
from flask import Flask, Response, jsonify


@dataclass
class Intrinsics:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    depth_scale: float


class RealSenseCapture:
    def __init__(
        self,
        color_width: int,
        color_height: int,
        depth_width: int,
        depth_height: int,
        fps: int,
        serial: str | None = None,
        jpeg_quality: int = 95,
    ):
        self.color_width = int(color_width)
        self.color_height = int(color_height)
        self.depth_width = int(depth_width)
        self.depth_height = int(depth_height)
        self.fps = fps
        self.serial = serial
        self.pipeline = rs.pipeline()
        self.config: rs.config | None = None
        self.align = rs.align(rs.stream.color)
        self.jpeg_quality = max(80, min(int(jpeg_quality), 100))
        self.depth_scale = 0.001
        self.intrinsics: Intrinsics | None = None
        self.active_profile: dict | None = None
        self.lock = threading.Lock()
        self.latest_color: np.ndarray | None = None
        self.latest_depth_mm: np.ndarray | None = None
        self.latest_stamp = 0.0
        self.latest_seq = 0
        self.start_error = ""
        self.running = False
        self.thread: threading.Thread | None = None

    def _log_devices(self):
        ctx = rs.context()
        devices = list(ctx.query_devices())
        if not devices:
            print("[camera] RealSense SDK sees 0 devices")
            return
        print(f"[camera] RealSense SDK sees {len(devices)} device(s):")
        for dev in devices:
            try:
                name = dev.get_info(rs.camera_info.name)
            except Exception:
                name = "unknown"
            try:
                serial = dev.get_info(rs.camera_info.serial_number)
            except Exception:
                serial = "unknown"
            print(f"[camera]   {name} serial={serial}")

    @staticmethod
    def _depth_scale_from_profile(profile) -> float:
        device = profile.get_device()
        for sensor in device.query_sensors():
            try:
                if sensor.is_depth_sensor():
                    return float(sensor.as_depth_sensor().get_depth_scale())
            except Exception:
                pass
        try:
            return float(device.first_depth_sensor().get_depth_scale())
        except Exception as exc:
            print(f"[camera] WARN: cannot read depth_scale, using 0.001: {exc}")
            return 0.001

    def _make_config(
        self,
        color_width: int,
        color_height: int,
        depth_width: int,
        depth_height: int,
    ):
        config = rs.config()
        if self.serial:
            config.enable_device(self.serial)
        config.enable_stream(
            rs.stream.color,
            int(color_width),
            int(color_height),
            rs.format.bgr8,
            self.fps,
        )
        config.enable_stream(
            rs.stream.depth,
            int(depth_width),
            int(depth_height),
            rs.format.z16,
            self.fps,
        )
        return config

    def start(self):
        self._log_devices()
        profile = None
        last_error = None
        requested = (
            self.color_width,
            self.color_height,
            self.depth_width,
            self.depth_height,
        )
        profiles = [requested]
        fallback = (640, 480, 640, 480)
        if requested != fallback:
            profiles.append(fallback)
        selected = None
        for profile_index, candidate in enumerate(profiles):
            label = "requested" if profile_index == 0 else "fallback"
            print(
                f"[camera] trying {label} profile: "
                f"color={candidate[0]}x{candidate[1]} "
                f"depth={candidate[2]}x{candidate[3]} fps={self.fps}"
            )
            self.config = self._make_config(*candidate)
            for attempt in range(1, 4):
                try:
                    profile = self.pipeline.start(self.config)
                    selected = candidate
                    break
                except Exception as exc:
                    last_error = exc
                    print(
                        f"[camera] pipeline.start {label} failed "
                        f"{attempt}/3: {exc}"
                    )
                    time.sleep(1.0)
            if profile is not None:
                break
        if profile is None:
            self.start_error = f"failed to start RealSense pipeline: {last_error}"
            raise RuntimeError(self.start_error)

        self.depth_scale = self._depth_scale_from_profile(profile)
        print(f"[camera] depth_scale={self.depth_scale}")

        color_profile = profile.get_stream(rs.stream.color).as_video_stream_profile()
        depth_profile = profile.get_stream(rs.stream.depth).as_video_stream_profile()
        intr = color_profile.get_intrinsics()
        depth_intr = depth_profile.get_intrinsics()
        self.intrinsics = Intrinsics(
            width=int(intr.width),
            height=int(intr.height),
            fx=float(intr.fx),
            fy=float(intr.fy),
            cx=float(intr.ppx),
            cy=float(intr.ppy),
            depth_scale=self.depth_scale,
        )
        self.active_profile = {
            "requested_color": [self.color_width, self.color_height],
            "requested_depth": [self.depth_width, self.depth_height],
            "color": [int(intr.width), int(intr.height)],
            "depth": [int(depth_intr.width), int(depth_intr.height)],
            "aligned_depth": [int(intr.width), int(intr.height)],
            "fps": int(self.fps),
            "fallback": bool(selected != requested),
            "jpeg_quality": int(self.jpeg_quality),
            "alignment": "librealsense_rs_align_depth_to_color",
            "image_provenance": "native_sensor_rgb_no_super_resolution",
        }
        print(f"[camera] active profile: {json.dumps(self.active_profile)}")

        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        try:
            self.pipeline.stop()
        except Exception:
            pass

    def _loop(self):
        while self.running:
            try:
                frames = self.pipeline.wait_for_frames(timeout_ms=2000)
                aligned = self.align.process(frames)
                color_frame = aligned.get_color_frame()
                depth_frame = aligned.get_depth_frame()
                if not color_frame or not depth_frame:
                    continue

                color = np.asanyarray(color_frame.get_data())
                depth_raw = np.asanyarray(depth_frame.get_data())
                if depth_raw.shape[:2] != color.shape[:2]:
                    print(
                        "[camera] aligned RGB-D shape mismatch: "
                        f"color={color.shape[1]}x{color.shape[0]} "
                        f"depth={depth_raw.shape[1]}x{depth_raw.shape[0]}"
                    )
                    continue
                if abs(self.depth_scale - 0.001) > 1e-6:
                    depth_mm = np.clip(
                        depth_raw.astype(np.float32) * self.depth_scale * 1000.0,
                        0,
                        65535,
                    ).astype(np.uint16)
                else:
                    depth_mm = depth_raw.astype(np.uint16)

                with self.lock:
                    self.latest_color = color.copy()
                    self.latest_depth_mm = depth_mm.copy()
                    self.latest_stamp = time.time()
                    self.latest_seq += 1
            except Exception as exc:
                print(f"[camera] capture error: {exc}")
                time.sleep(0.2)

    def get_frame(self):
        with self.lock:
            if self.latest_color is None or self.latest_depth_mm is None:
                return None
            return (
                self.latest_seq,
                self.latest_stamp,
                self.latest_color.copy(),
                self.latest_depth_mm.copy(),
            )


def encode_image(ext: str, image: np.ndarray, jpeg_quality: int = 95) -> bytes:
    params = []
    if ext.lower() in {".jpg", ".jpeg"}:
        params = [cv2.IMWRITE_JPEG_QUALITY, max(80, min(int(jpeg_quality), 100))]
    ok, encoded = cv2.imencode(ext, image, params)
    if not ok:
        raise RuntimeError(f"failed to encode {ext}")
    return encoded.tobytes()


def b64encode_image(ext: str, image: np.ndarray, jpeg_quality: int = 95) -> str:
    return base64.b64encode(
        encode_image(ext, image, jpeg_quality=jpeg_quality)
    ).decode("ascii")


def create_app(capture: RealSenseCapture) -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        frame = capture.get_frame()
        return jsonify({
            "ok": frame is not None,
            "seq": frame[0] if frame else None,
            "stamp": frame[1] if frame else None,
            "intrinsics": asdict(capture.intrinsics) if capture.intrinsics else None,
            "profile": capture.active_profile,
            "error": capture.start_error or None,
        })

    @app.get("/intrinsics")
    def intrinsics():
        if capture.intrinsics is None:
            return jsonify({"ok": False, "error": "camera not ready"}), 503
        return jsonify({"ok": True, **asdict(capture.intrinsics)})

    @app.get("/frame")
    def frame():
        data = capture.get_frame()
        if data is None or capture.intrinsics is None:
            return jsonify({"ok": False, "error": capture.start_error or "no frame"}), 503
        seq, stamp, color, depth_mm = data
        return app.response_class(
            response=json.dumps({
                "ok": True,
                "seq": seq,
                "stamp": stamp,
                "intrinsics": asdict(capture.intrinsics),
                "profile": capture.active_profile,
                "color_encoding": "bgr8",
                "depth_encoding": "16UC1",
                "color_jpeg_b64": b64encode_image(
                    ".jpg",
                    color,
                    jpeg_quality=capture.jpeg_quality,
                ),
                "depth_png_b64": b64encode_image(".png", depth_mm),
            }),
            status=200,
            mimetype="application/json",
        )

    @app.get("/snapshot.jpg")
    def snapshot():
        data = capture.get_frame()
        if data is None:
            return Response("no frame", status=503)
        _, _, color, _ = data
        try:
            encoded = encode_image(
                ".jpg",
                color,
                jpeg_quality=capture.jpeg_quality,
            )
        except RuntimeError:
            return Response("encode failed", status=500)
        return Response(encoded, mimetype="image/jpeg")

    @app.get("/stream.mjpg")
    def stream():
        def generate():
            last_seq = -1
            while True:
                data = capture.get_frame()
                if data is None:
                    time.sleep(0.1)
                    continue
                seq, _, color, _ = data
                if seq == last_seq:
                    time.sleep(0.02)
                    continue
                last_seq = seq
                try:
                    encoded = encode_image(
                        ".jpg",
                        color,
                        jpeg_quality=capture.jpeg_quality,
                    )
                except RuntimeError:
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + encoded
                    + b"\r\n"
                )

        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Legacy option: set both color and depth width.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Legacy option: set both color and depth height.",
    )
    parser.add_argument("--color-width", type=int, default=None)
    parser.add_argument("--color-height", type=int, default=None)
    parser.add_argument("--depth-width", type=int, default=None)
    parser.add_argument("--depth-height", type=int, default=None)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--serial", default=None)
    args = parser.parse_args()

    color_width = args.color_width or args.width or 1280
    color_height = args.color_height or args.height or 720
    depth_width = args.depth_width or args.width or 640
    depth_height = args.depth_height or args.height or 480
    capture = RealSenseCapture(
        color_width,
        color_height,
        depth_width,
        depth_height,
        args.fps,
        args.serial,
        args.jpeg_quality,
    )
    app = create_app(capture)
    try:
        try:
            capture.start()
        except Exception as exc:
            capture.start_error = str(exc)
            print(f"[camera] capture start failed; HTTP health remains available: {exc}")
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        capture.stop()


if __name__ == "__main__":
    main()
