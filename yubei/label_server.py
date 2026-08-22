"""Local browser annotation server for one yubei dataset session."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import posixpath
import shutil
import threading
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

import cv2

try:
    from .paths import load_json, save_json_atomic
except ImportError:  # direct module execution
    from paths import load_json, save_json_atomic


CLASSES = {"rice": 0, "flower": 1}
STATUSES = {"unlabelled", "labelled", "ambiguous", "skipped"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _safe_name(root: Path, name: str) -> Path:
    if not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("image path is outside the session")
    path = (root / name).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("image path is outside the session") from exc
    return path


class LabelStore:
    def __init__(self, session_root: Path):
        self.root = Path(session_root).expanduser().resolve()
        self.images_dir = (self.root / "images").resolve()
        self.labels_dir = (self.root / "labels").resolve()
        self.ambiguous_dir = (self.root / "ambiguous").resolve()
        self._capture_metadata: dict[str, dict] = {}
        if not self.images_dir.is_dir() or not self.labels_dir.is_dir():
            raise ValueError("session must contain images/ and labels/")
        manifest_path = self.root / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = load_json(manifest_path)
                for item in manifest.get("images", []):
                    filename = str(item.get("filename", ""))
                    if filename.startswith("images/"):
                        filename = filename.removeprefix("images/")
                    if filename:
                        self._capture_metadata[filename] = dict(item)
            except (OSError, ValueError, json.JSONDecodeError):
                self._capture_metadata = {}

    def _image_path(self, name: str) -> Path:
        path = _safe_name(self.images_dir, name)
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            raise ValueError("image does not exist")
        return path

    def _label_path(self, name: str) -> Path:
        image = self._image_path(name)
        relative = image.relative_to(self.images_dir)
        return self.labels_dir / relative.with_suffix(".json")

    def _yolo_path(self, name: str) -> Path:
        image = self._image_path(name)
        relative = image.relative_to(self.images_dir)
        return self.labels_dir / relative.with_suffix(".txt")

    def _dimensions(self, name: str) -> tuple[int, int] | None:
        image = cv2.imread(str(self._image_path(name)), cv2.IMREAD_UNCHANGED)
        if image is None:
            return None
        return int(image.shape[1]), int(image.shape[0])

    def list_images(self) -> list[dict]:
        values = []
        for path in sorted(self.images_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            name = path.relative_to(self.images_dir).as_posix()
            try:
                label = self.load(name)
            except ValueError:
                label = {"status": "unlabelled", "boxes": []}
            capture = self._capture_metadata.get(name, {})
            values.append({
                "name": name,
                "status": label["status"],
                "box_count": len(label["boxes"]),
                "capture_tag": capture.get("capture_tag", "neutral"),
                "seq": capture.get("seq"),
                "quality": capture.get("quality"),
                "duplicate_of": capture.get("duplicate_of", ""),
            })
        return values

    def load(self, name: str) -> dict:
        self._image_path(name)
        path = self._label_path(name)
        if not path.exists():
            return {
                "image": name,
                "status": "unlabelled",
                "boxes": [],
                "capture": self._capture_metadata.get(name, {}),
            }
        value = load_json(path)
        value.setdefault("image", name)
        value.setdefault("status", "unlabelled")
        value.setdefault("boxes", [])
        value.setdefault("capture", self._capture_metadata.get(name, {}))
        self._validate(name, value["boxes"], value["status"])
        return value

    @staticmethod
    def _validate_box(box: dict) -> None:
        if not isinstance(box, dict) or box.get("class_name") not in CLASSES:
            raise ValueError("class must be rice or flower")
        for key in ("x", "y", "width", "height"):
            if key not in box:
                raise ValueError(f"box missing {key}")
            try:
                value = float(box[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"box {key} is not numeric") from exc
            if key in {"width", "height"} and value <= 0:
                raise ValueError("box dimensions must be positive")
            if key in {"x", "y"} and value < 0:
                raise ValueError("box bounds cannot be negative")

    def _validate(self, name: str, boxes: list[dict], status: str, image_width: int | None = None, image_height: int | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"invalid label status: {status}")
        dimensions = self._dimensions(name)
        width, height = dimensions or (image_width, image_height)
        for box in boxes:
            self._validate_box(box)
            if width is not None and float(box["x"]) + float(box["width"]) > width + 1e-6:
                raise ValueError("box bounds exceed image width")
            if height is not None and float(box["y"]) + float(box["height"]) > height + 1e-6:
                raise ValueError("box bounds exceed image height")

    def save(self, name: str, boxes: list[dict], status: str) -> None:
        self._image_path(name)
        self._validate(name, boxes, status)
        save_json_atomic(
            self._label_path(name),
            {
                "image": name,
                "status": status,
                "boxes": boxes,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        yolo_path = self._yolo_path(name)
        if status == "labelled":
            self.export_yolo(name)
        elif yolo_path.exists():
            yolo_path.unlink()
        if status == "ambiguous":
            self.ambiguous_dir.mkdir(parents=True, exist_ok=True)

    def export_yolo(self, name: str, image_width: int | None = None, image_height: int | None = None) -> Path:
        label = self.load(name)
        dimensions = self._dimensions(name)
        width, height = dimensions or (image_width, image_height)
        if not width or not height:
            raise ValueError("image dimensions are required for YOLO export")
        output = self._yolo_path(name)
        output.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for box in label["boxes"]:
            x = float(box["x"])
            y = float(box["y"])
            w = float(box["width"])
            h = float(box["height"])
            lines.append(f"{CLASSES[box['class_name']]} {(x + w / 2) / width:g} {(y + h / 2) / height:g} {w / width:g} {h / height:g}")
        output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return output


class LabelRequestHandler(BaseHTTPRequestHandler):
    store: LabelStore
    static_dir: Path

    def log_message(self, format, *args):  # noqa: A002
        return

    def _json(self, status: int, value: dict):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, path: str):
        requested = (self.static_dir / path.lstrip("/")).resolve()
        try:
            requested.relative_to(self.static_dir.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not requested.is_file():
            self.send_error(404)
            return
        data = requested.read_bytes()
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(requested.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/images":
                return self._json(200, {"images": self.store.list_images()})
            if path.startswith("/api/labels/"):
                return self._json(200, self.store.load(path.removeprefix("/api/labels/")))
            if path.startswith("/media/"):
                image = self.store._image_path(path.removeprefix("/media/"))
                data = image.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg" if image.suffix.lower() in {".jpg", ".jpeg"} else "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._static("/index.html" if path == "/" else path)
        except (ValueError, FileNotFoundError) as exc:
            self._json(400, {"error": str(exc)})

    def do_PUT(self):  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/labels/"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            self.store.save(parsed.path.removeprefix("/api/labels/"), value.get("boxes", []), value.get("status", "unlabelled"))
            self._json(200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动本地 YOLO bbox 标注网页")
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    store = LabelStore(args.session)
    handler = type("YubeiLabelHandler", (LabelRequestHandler,), {})
    handler.store = store
    handler.static_dir = Path(__file__).resolve().parent / "label_ui"
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"标注网页: http://{args.host}:{args.port}/")
    if args.open_browser:
        threading.Timer(
            0.2,
            lambda: webbrowser.open(f"http://{args.host}:{args.port}/"),
        ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
