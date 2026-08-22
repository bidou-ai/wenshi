"""Enter-driven RGB dataset capture for YOLO training."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
import sys
import threading
from typing import TextIO

import cv2
import numpy as np
import yaml

try:
    from .capture_ui import close_preview, show_rgb
    from .http_camera import CameraFrame, HttpCameraClient
    from .paths import SessionPaths, save_json_atomic
    from .schemas import DatasetManifest
    from .capture_quality import assess_image, image_signature, signature_distance
except ImportError:  # direct `python yubei/dataset_capture.py`
    from capture_ui import close_preview, show_rgb
    from http_camera import CameraFrame, HttpCameraClient
    from paths import SessionPaths, save_json_atomic
    from schemas import DatasetManifest
    from capture_quality import assess_image, image_signature, signature_distance


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


_NO_COMMAND = object()


def camera_url_from_config(config_path: Path) -> str:
    value = yaml.safe_load(Path(config_path).expanduser().read_text(encoding="utf-8")) or {}
    url = str(value.get("camera", {}).get("server_url", "")).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"camera.server_url 无效: {url!r}")
    return url


class AsyncCommandReader:
    def __init__(self, input_stream: TextIO):
        self.input_stream = input_stream
        self.commands: Queue[str | None] = Queue()
        self.thread = threading.Thread(target=self._read, name="dataset-capture-input", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _read(self) -> None:
        while True:
            command = self.input_stream.readline()
            if command == "":
                self.commands.put(None)
                return
            self.commands.put(command)

    def poll(self, timeout_s: float = 0.03):
        try:
            return self.commands.get(timeout=max(float(timeout_s), 0.0))
        except Empty:
            return _NO_COMMAND


class CaptureSession:
    def __init__(self, camera, paths: SessionPaths, jpeg_quality: int = 95, capture_tag: str = "neutral"):
        self.camera = camera
        self.paths = paths
        self.jpeg_quality = max(80, min(int(jpeg_quality), 100))
        if capture_tag not in {"neutral", "rice", "flower"}:
            raise ValueError("capture_tag must be neutral, rice or flower")
        self.capture_tag = capture_tag
        self.manifest = DatasetManifest()
        self.manifest.write(self.paths.manifest_path)
        self._number = 0
        self._signatures: list[tuple[int, str]] = []
        self._quality_warnings = 0

    def capture_one(
        self,
        displayed_image: np.ndarray | None = None,
        frame: CameraFrame | None = None,
    ) -> Path:
        if frame is None and displayed_image is None:
            frame = self.camera.frame()
        image = frame.color if frame is not None else displayed_image
        if image is None:
            raise ValueError("no RGB frame available")
        self._number += 1
        filename = f"{self._number:06d}.jpg"
        output = self.paths.images_dir / filename
        info = capture_image(image, output, self.jpeg_quality)
        quality = assess_image(image)
        signature = image_signature(image)
        duplicate_of = ""
        for previous_signature, previous_name in reversed(self._signatures):
            if signature_distance(signature, previous_signature) <= 3:
                duplicate_of = previous_name
                break
        if not quality["ok"] or duplicate_of:
            self._quality_warnings += 1
        self._signatures.append((signature, filename))
        metadata = {
            "capture_tag": self.capture_tag,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "quality": quality,
            "duplicate_of": duplicate_of,
        }
        if frame is not None:
            metadata.update({"seq": int(frame.seq), "camera_received_at": float(frame.received_at)})
        self.manifest.add_image(
            f"images/{filename}",
            info["width"],
            info["height"],
            "captured",
            metadata=metadata,
        )
        self.manifest.write(self.paths.manifest_path)
        return output

    def run(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout, preview: bool = True) -> int:
        output_stream.write(
            "回车保存当前 RGB；f=开花批次，r=水稻批次，n=普通批次（只切换不拍照），q=结束。\n"
        )
        output_stream.write(f"当前批次: {self.capture_tag}\n")
        output_stream.flush()
        saved = 0
        reader = AsyncCommandReader(input_stream) if preview else None
        if reader is not None:
            reader.start()
        try:
            while True:
                displayed_image = None
                frame = None
                if preview:
                    try:
                        frame = self.camera.frame()
                        displayed_image = frame.color
                        if not show_rgb(
                            frame.color,
                            title=f"yubei RGB [{self.capture_tag}] saved={saved}",
                        ):
                            break
                    except Exception as exc:
                        output_stream.write(f"预览失败: {exc}\n")
                    command = reader.poll()
                    if command is _NO_COMMAND:
                        continue
                else:
                    command = input_stream.readline()
                if command is None or command == "":
                    break
                if command.strip().lower() == "q":
                    break
                tag_commands = {"f": "flower", "r": "rice", "n": "neutral"}
                if command.strip().lower() in tag_commands:
                    self.capture_tag = tag_commands[command.strip().lower()]
                    output_stream.write(f"已切换采集批次: {self.capture_tag}\n")
                    output_stream.flush()
                    continue
                if command.strip() != "":
                    output_stream.write("请输入回车保存，或输入 q 结束。\n")
                    continue
                try:
                    path = self.capture_one(displayed_image, frame=frame)
                except Exception as exc:
                    output_stream.write(f"保存失败: {exc}\n")
                    continue
                saved += 1
                item = self.manifest.images[-1]
                quality = item.get("quality", {})
                notes = []
                if not quality.get("ok", False):
                    notes.append("质量提醒=" + ",".join(quality.get("reasons", [])))
                if item.get("duplicate_of"):
                    notes.append(f"相似于={item['duplicate_of']}")
                note = "；" + "；".join(notes) if notes else "；质量检查通过"
                output_stream.write(
                    f"已保存 {path.name} [{self.capture_tag}]{note}: {path}\n"
                )
                output_stream.flush()
        finally:
            if preview:
                close_preview()
            save_json_atomic(
                self.paths.root / "session_summary.json",
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "images_saved": saved,
                    "quality_warnings": self._quality_warnings,
                    "capture_tags": {
                        tag: sum(1 for item in self.manifest.images if item.get("capture_tag") == tag)
                        for tag in ("flower", "rice", "neutral")
                    },
                    "session": str(self.paths.root),
                },
            )
        return saved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按回车采集 YOLO RGB 数据集")
    parser.add_argument("--config", type=Path, default=Path("config/wenshi.yaml"))
    parser.add_argument("--url", default="", help="临时覆盖配置中的相机地址")
    parser.add_argument("--output", type=Path, default=Path("yubei/data"))
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--focus", choices=("neutral", "rice", "flower"), default="neutral")
    args = parser.parse_args(argv)
    paths = SessionPaths.create(args.output)
    camera_url = args.url.strip().rstrip("/") or camera_url_from_config(args.config)
    camera = HttpCameraClient(camera_url, timeout_s=args.timeout)
    saved = CaptureSession(camera, paths, args.jpeg_quality, args.focus).run(preview=args.preview)
    print(json.dumps({"session": str(paths.root), "images_saved": saved}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
