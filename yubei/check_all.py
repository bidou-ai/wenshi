"""Configuration-driven, read-only field checks for the yubei launcher."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

try:
    from .camera_check import probe_camera
    from .network_check import default_route, probe_tcp
except ImportError:  # direct module execution
    from camera_check import probe_camera
    from network_check import default_route, probe_tcp


def _config(path: Path) -> dict:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("配置文件必须是 YAML 对象")
    return value


def _camera_url(value: object) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"camera.server_url 无效: {url!r}")
    return url


def run_checks(config_path: Path, samples: int = 10, timeout_s: float = 2.0) -> dict:
    config = _config(config_path)
    agv = config.get("agv", {})
    jaka = config.get("jaka", {})
    camera = config.get("camera", {})
    camera_url = _camera_url(camera.get("server_url"))
    results = {
        "config": str(Path(config_path).expanduser().resolve()),
        "source_ip": default_route(),
        "agv_status": probe_tcp(agv.get("ip", ""), int(agv.get("status_port", 19204)), min(float(timeout_s), 1.0)).to_dict(),
        "agv_motion": probe_tcp(agv.get("ip", ""), int(agv.get("motion_port", 19205)), min(float(timeout_s), 1.0)).to_dict(),
        "jaka": probe_tcp(jaka.get("ip", ""), int(jaka.get("port", 10001)), min(float(timeout_s), 1.0)).to_dict(),
        "camera": probe_camera(camera_url, samples=max(int(samples), 0), timeout_s=timeout_s),
    }
    results["ok"] = bool(
        results["agv_status"].get("ok")
        and results["agv_motion"].get("ok")
        and results["jaka"].get("ok")
        and results["camera"].get("ok")
    )
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按正式配置只读检查 AGV、JAKA 和 D435")
    parser.add_argument("--config", type=Path, default=Path("config/wenshi.yaml"))
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        result = run_checks(args.config, args.samples, args.timeout)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
