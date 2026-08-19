"""Read-only patrol results dashboard HTTP server."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from .admin import AdminActions
    from .run_index import MediaResolver, RunIndex
except ImportError:  # direct: python3 dashboard/server.py
    from admin import AdminActions
    from run_index import MediaResolver, RunIndex


class DashboardHandler(BaseHTTPRequestHandler):
    index: RunIndex
    admin: AdminActions
    static_dir: Path

    def log_message(self, format, *args):  # noqa: A002
        return

    def _write_json(self, status: int, value: dict):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, name: str):
        path = (self.static_dir / name.lstrip("/")).resolve()
        try:
            path.relative_to(self.static_dir.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/runs":
                return self._write_json(200, {"runs": self.index.list_runs()})
            if path.startswith("/api/runs/") and "/targets/" not in path:
                return self._write_json(200, self.index.load_run(path.removeprefix("/api/runs/")))
            if path.startswith("/api/runs/") and "/targets/" in path:
                run_id, target_id = path.removeprefix("/api/runs/").split("/targets/", 1)
                return self._write_json(200, self.index.load_target(run_id, target_id))
            if path.startswith("/media/"):
                parts = path.removeprefix("/media/").split("/", 2)
                if len(parts) != 3:
                    raise ValueError("invalid media path")
                media = MediaResolver(self.index.root).resolve(parts[0], parts[1], parts[2])
                data = media.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._static("index.html" if path == "/" else path)
        except (ValueError, FileNotFoundError) as exc:
            self._write_json(400, {"error": str(exc)})

    def do_POST(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/admin/auth":
                token = self.admin.authenticate(value.get("pin", ""))
                return self._write_json(200 if token else 403, {"ok": bool(token), "token": token})
            token = value.get("token", "")
            if path == "/api/admin/delete":
                return self._write_json(200, self.admin.soft_delete(value["run_id"], value.get("target_id"), token))
            if path == "/api/admin/reset-dedupe":
                return self._write_json(200, self.admin.reset_dedupe(value["run_id"], token))
            self.send_error(404)
        except (ValueError, KeyError, PermissionError, FileNotFoundError, json.JSONDecodeError) as exc:
            self._write_json(400, {"error": str(exc)})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动 Wenshi 巡检结果后台")
    parser.add_argument("--root", type=Path, default=Path("runtime/runs"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--pin", default="")
    args = parser.parse_args(argv)
    index = RunIndex(args.root)
    admin = AdminActions(args.root, args.pin)
    handler = type("WenshiDashboardHandler", (DashboardHandler,), {})
    handler.index = index
    handler.admin = admin
    handler.static_dir = Path(__file__).resolve().parent / "static"
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Wenshi 后台: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
