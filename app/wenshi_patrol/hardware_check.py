"""按正式配置检查 AGV、JAKA 和 D435 的网络可达性。"""

from __future__ import annotations

import argparse
import json
import socket
import urllib.request
from typing import Any

from .config import load_config


def configured_endpoints(config: dict[str, Any]) -> dict[str, tuple[str, int] | str]:
    agv = config["agv"]
    jaka = config["jaka"]
    camera_url = str(config["camera"]["server_url"]).rstrip("/")
    return {
        "agv_status": (str(agv["ip"]), int(agv.get("status_port", 19204))),
        "agv_motion": (str(agv["ip"]), int(agv.get("motion_port", 19205))),
        "jaka": (str(jaka["ip"]), int(jaka.get("port", 10001))),
        "camera_health": f"{camera_url}/health",
    }


def _check_tcp(name: str, host: str, port: int, timeout_s: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            pass
    except OSError as exc:
        return False, f"{name} {host}:{port} 不可达: {exc}"
    return True, f"{name} {host}:{port} TCP 可达"


def _check_camera(url: str, timeout_s: float) -> tuple[bool, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, f"D435 health 不可达: {url}: {exc}"
    if not body.get("ok"):
        return False, f"D435 服务已响应但未提供有效帧: {body}"
    return True, f"D435 服务和有效帧正常: seq={body.get('seq')}"


def check_hardware(config: dict[str, Any], timeout_s: float = 2.0) -> list[tuple[bool, str]]:
    endpoints = configured_endpoints(config)
    return [
        _check_tcp("AGV 状态", *endpoints["agv_status"], timeout_s),
        _check_tcp("AGV 运动", *endpoints["agv_motion"], timeout_s),
        _check_tcp("JAKA", *endpoints["jaka"], timeout_s),
        _check_camera(str(endpoints["camera_health"]), timeout_s),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wenshi 现场硬件网络检查")
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeout", type=float, default=2.0)
    arguments = parser.parse_args(argv)
    results = check_hardware(load_config(arguments.config), max(arguments.timeout, 0.1))
    failed = False
    for ok, message in results:
        print(f"[{'OK' if ok else 'FAIL'}] {message}")
        failed = failed or not ok
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

