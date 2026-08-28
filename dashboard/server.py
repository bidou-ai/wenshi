"""Read-only patrol results dashboard HTTP server."""

from __future__ import annotations

import argparse
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
import re

try:
    from .admin import AdminActions
    from .export import export_csv
    from .phenotype_index import PhenotypeIndex
    from .run_index import MediaResolver, RunIndex
except ImportError:  # direct: python3 dashboard/server.py
    from admin import AdminActions
    from export import export_csv
    from phenotype_index import PhenotypeIndex
    from run_index import MediaResolver, RunIndex


_PHENOTYPE_RUN = re.compile(r"^/api/phenotype/runs/([^/]+)$")
_PHENOTYPE_PLANT = re.compile(r"^/api/phenotype/runs/([^/]+)/plants/([^/]+)$")
_PHENOTYPE_MEDIA = re.compile(r"^/api/phenotype/runs/([^/]+)/plants/([^/]+)/media/(left|center|right)/(color\.jpg|depth\.png|frame\.json)$")
_PHENOTYPE_EXPORT = re.compile(r"^/api/phenotype/export/([^/]+)$")


def parse_phenotype_path(path: str) -> tuple[str, ...] | None:
    """Parse only complete, known phenotype API paths."""
    if path == "/api/phenotype/runs":
        return ("runs",)
    for kind, pattern in (
        ("export", _PHENOTYPE_EXPORT),
        ("media", _PHENOTYPE_MEDIA),
        ("plant", _PHENOTYPE_PLANT),
        ("run", _PHENOTYPE_RUN),
    ):
        match = pattern.fullmatch(path)
        if match:
            return (kind, *match.groups())
    return None


class DashboardHandler(BaseHTTPRequestHandler):
    index: RunIndex
    phenotype_index: PhenotypeIndex
    admin: AdminActions
    static_dir: Path
    phenotype_requires_auth = False

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

    def _require_phenotype_auth(self) -> None:
        if self.phenotype_requires_auth:
            self.admin._check(self.headers.get("X-Wenshi-Token", ""))

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/runs":
                return self._write_json(200, {"runs": self.index.list_runs()})
            phenotype_route = parse_phenotype_path(path)
            if phenotype_route:
                self._require_phenotype_auth()
                kind, *parts = phenotype_route
                if kind == "runs":
                    return self._write_json(200, {"runs": self.phenotype_index.list_runs()})
                if kind == "export":
                    from io import StringIO
                    output = StringIO()
                    export_csv(self.phenotype_index._run_path(parts[0]), output)
                    data = output.getvalue().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{parts[0]}.csv"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if kind == "media":
                    media = self.phenotype_index.resolve_media(*parts)
                    data = media.read_bytes()
                    filename = parts[-1]
                    content_type = "image/png" if filename.endswith(".png") else "image/jpeg" if filename.endswith(".jpg") else "application/json; charset=utf-8"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if kind == "plant":
                    return self._write_json(200, self.phenotype_index.load_plant(*parts))
                return self._write_json(200, self.phenotype_index.load_run(parts[0]))
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
        except PermissionError as exc:
            self._write_json(403, {"error": str(exc)})
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


def dashboard_host_policy(host: str, pin: str) -> bool:
    """Return whether phenotype reads need a token for this listening address."""
    normalized = str(host).strip().strip("[]").lower()
    try:
        loopback = ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        loopback = normalized == "localhost"
    if not loopback and not pin:
        raise ValueError("非本机监听必须通过 --pin 配置管理员 PIN")
    return not loopback


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动 Wenshi 巡检结果后台")
    parser.add_argument("--root", type=Path, default=Path("runtime/runs"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--pin", default="")
    args = parser.parse_args(argv)
    try:
        phenotype_requires_auth = dashboard_host_policy(args.host, args.pin)
    except ValueError as exc:
        parser.error(str(exc))
    index = RunIndex(args.root)
    admin = AdminActions(args.root, args.pin)
    handler = type("WenshiDashboardHandler", (DashboardHandler,), {})
    handler.index = index
    handler.phenotype_index = PhenotypeIndex(args.root)
    handler.admin = admin
    handler.static_dir = Path(__file__).resolve().parent / "static"
    handler.phenotype_requires_auth = phenotype_requires_auth
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
