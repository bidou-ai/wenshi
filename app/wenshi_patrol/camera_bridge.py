"""Publish the Windows D435 HTTP stream as ROS2 image topics."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String

from .config import load_config


class CameraBridgeNode(Node):
    def __init__(self, config: dict):
        super().__init__("wenshi_camera_bridge")
        camera = config["camera"]
        topics = config["topics"]
        self._url = str(camera["server_url"]).rstrip("/")
        self._period = 1.0 / max(float(camera.get("rate_hz", 10.0)), 0.2)
        self._timeout = float(camera.get("timeout_s", 1.5))
        self._frame_id = str(camera.get("frame_id", "camera_color_optical_frame"))
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._last_seq = None
        self._errors = 0

        self._color_pub = self.create_publisher(Image, topics["color"], 5)
        self._depth_pub = self.create_publisher(Image, topics["depth"], 5)
        self._info_pub = self.create_publisher(CameraInfo, topics["camera_info"], 5)
        self._status_pub = self.create_publisher(String, topics["camera_status"], 10)
        self.create_timer(self._period, self._tick)
        self._log = self._make_file_logger()
        self._log.info("camera bridge start url=%s", self._url)

    @staticmethod
    def _make_file_logger() -> logging.Logger:
        logger = logging.getLogger("wenshi_camera_file")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            configured = os.environ.get("WENSHI_RUN_DIR", ".")
            run_dir = Path(configured).resolve()
            run_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(run_dir / "camera.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        return logger

    def _status(self, value: str):
        message = String()
        message.data = value
        self._status_pub.publish(message)

    @staticmethod
    def _decode(value: str, flags: int) -> np.ndarray:
        raw = base64.b64decode(value.encode("ascii"))
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), flags)
        if image is None:
            raise RuntimeError("图像解码失败")
        return image

    def _fetch(self) -> dict:
        with self._opener.open(f"{self._url}/frame", timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _camera_info(self, intrinsics: dict, stamp) -> CameraInfo:
        message = CameraInfo()
        message.header.stamp = stamp
        message.header.frame_id = self._frame_id
        message.width = int(intrinsics["width"])
        message.height = int(intrinsics["height"])
        fx = float(intrinsics["fx"])
        fy = float(intrinsics["fy"])
        cx = float(intrinsics["cx"])
        cy = float(intrinsics["cy"])
        message.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        message.d = [0.0] * 5
        message.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        message.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        message.distortion_model = "plumb_bob"
        return message

    def _image_message(self, image: np.ndarray, encoding: str, stamp) -> Image:
        image = np.ascontiguousarray(image)
        message = Image()
        message.header.stamp = stamp
        message.header.frame_id = self._frame_id
        message.height = int(image.shape[0])
        message.width = int(image.shape[1])
        message.encoding = encoding
        message.is_bigendian = False
        message.step = int(image.strides[0])
        message.data = image.tobytes()
        return message

    def _tick(self):
        try:
            packet = self._fetch()
            if not packet.get("ok"):
                raise RuntimeError(str(packet.get("error", "相机未就绪")))
            seq = packet.get("seq")
            if seq == self._last_seq:
                self._status(f"stale:{self._url}:seq={seq}")
                return
            self._last_seq = seq
            color = self._decode(packet["color_jpeg_b64"], cv2.IMREAD_COLOR)
            depth = self._decode(packet["depth_png_b64"], cv2.IMREAD_UNCHANGED)
            if depth.dtype != np.uint16:
                depth = depth.astype(np.uint16)
            stamp = self.get_clock().now().to_msg()

            color_message = self._image_message(color, "bgr8", stamp)
            depth_message = self._image_message(depth, "16UC1", stamp)
            info_message = self._camera_info(packet["intrinsics"], stamp)
            self._color_pub.publish(color_message)
            self._depth_pub.publish(depth_message)
            self._info_pub.publish(info_message)
            self._errors = 0
            self._status(f"ok:{self._url}:seq={seq}")
        except Exception as exc:
            self._errors += 1
            if self._errors == 1 or self._errors % 10 == 0:
                self.get_logger().warning(f"D435 HTTP 获取失败: {exc}")
                self._log.warning("fetch failed count=%s error=%s", self._errors, exc)
            self._status(f"error:{self._url}:count={self._errors}:{exc}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    arguments, ros_args = parser.parse_known_args(argv)
    config = load_config(arguments.config)
    rclpy.init(args=ros_args)
    node = CameraBridgeNode(config)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
