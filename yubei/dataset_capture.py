"""Enter-driven RGB dataset capture for YOLO training."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import TextIO

import cv2
import numpy as np

try:
    from .capture_ui import close_preview, show_rgb
    from .http_camera import CameraFrame, HttpCameraClient
    from .paths import SessionPaths, save_json_atomic
    from .schemas import DatasetManifest
except ImportError:  # direct `python yubei/dataset_capture.py`
    from capture_ui import close_preview, show_rgb
    from http_camera import CameraFrame, HttpCameraClient
    from paths import SessionPaths, save_json_atomic
    from schemas import DatasetManifest


def capture_image(image: np.ndarray, path: Path, jpeg_quality: int = 95) -> dict:
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("cannot save an empty image")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    quality = max(80, min(int(jpeg_quality), 100))
    ok = cv2.imwrite(str(output), image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise OSError(f"failed to write image: {output}")
    return {"width": int(image.shape[1]), "height": int(image.shape[0]), "jpeg_quality": quality}


class CaptureSession:
    def __init__(self, camera, paths: SessionPaths, jpeg_quality: int = 95):
        self.camera = camera
        self.paths = paths
        self.jpeg_quality = max(80, min(int(jpeg_quality), 100))
        self.manifest = DatasetManifest()
        self.manifest.write(self.paths.manifest_path)
        self._number = 0

    def capture_one(self) -> Path:
        frame: CameraFrame = self.camera.frame()
        self._number += 1
        filename = f"{self._number:06d}.jpg"
        output = self.paths.images_dir / filename
        info = capture_image(frame.color, output, self.jpeg_quality)
        self.manifest.add_image(f"images/{filename}", info["width"], info["height"], "captured")
        self.manifest.write(self.paths.manifest_path)
        return output

    def run(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout, preview: bool = True) -> int:
        output_stream.write("回车保存一张 RGB JPG；你可以在两次回车之间手动移动机械臂；输入 q 结束。\n")
        output_stream.flush()
        saved = 0
        try:
            while True:
                if preview:
                    try:
                        frame = self.camera.frame()
                        if not show_rgb(frame.color):
                            break
                    except Exception as exc:
                        output_stream.write(f"预览失败: {exc}\n")
                command = input_stream.readline()
                if command == "":
                    break
                if command.strip().lower() == "q":
                    break
                if command.strip() != "":
                    output_stream.write("请输入回车保存，或输入 q 结束。\n")
                    continue
                try:
                    path = self.capture_one()
                except Exception as exc:
                    output_stream.write(f"保存失败: {exc}\n")
                    continue
                saved += 1
                output_stream.write(f"已保存 {path.name}: {path}\n")
                output_stream.flush()
        finally:
            if preview:
                close_preview()
            save_json_atomic(
                self.paths.root / "session_summary.json",
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "images_saved": saved,
                    "session": str(self.paths.root),
                },
            )
        return saved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按回车采集 YOLO RGB 数据集")
    parser.add_argument("--url", default="http://192.168.192.203:18080")
    parser.add_argument("--output", type=Path, default=Path("yubei/data"))
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(argv)
    paths = SessionPaths.create(args.output)
    camera = HttpCameraClient(args.url, timeout_s=args.timeout)
    saved = CaptureSession(camera, paths, args.jpeg_quality).run(preview=args.preview)
    print(json.dumps({"session": str(paths.root), "images_saved": saved}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

