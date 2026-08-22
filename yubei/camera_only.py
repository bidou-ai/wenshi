"""Camera-only preflight for RGB dataset capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

try:
    from .camera_check import probe_camera
except ImportError:  # direct module execution
    from camera_check import probe_camera


def camera_url_from_config(config_path: Path) -> str:
    value = yaml.safe_load(Path(config_path).expanduser().read_text(encoding="utf-8")) or {}
    url = str(value.get("camera", {}).get("server_url", "")).strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"camera.server_url 无效: {url!r}")
    return url


def run_check(config_path: Path, samples: int = 10, timeout_s: float = 2.0) -> dict:
    url = camera_url_from_config(config_path)
    report = probe_camera(url, samples=max(int(samples), 1), timeout_s=float(timeout_s))
    report["scope"] = "camera-only; AGV/JAKA were not checked or commanded"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只检查 D435，不连接或控制 AGV/JAKA")
    parser.add_argument("--config", type=Path, default=Path("config/wenshi.yaml"))
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        result = run_check(args.config, args.samples, args.timeout)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
