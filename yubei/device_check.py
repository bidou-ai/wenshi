"""Read-only AGV/JAKA connectivity report."""

from __future__ import annotations

import argparse
import json
from typing import Any

try:
    from .device_protocol import read_only_probe
    from .network_check import probe_tcp
except ImportError:  # direct `python yubei/device_check.py`
    from device_protocol import read_only_probe
    from network_check import probe_tcp


def _as_dict(value: Any) -> dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def read_only_device_report(agv_host: str, jaka_host: str, timeout_s: float = 1.0) -> dict[str, Any]:
    return {
        "agv": _as_dict(probe_tcp(agv_host, 19204, timeout_s)),
        "jaka": _as_dict(probe_tcp(jaka_host, 10001, timeout_s)),
        "scope": "read-only connectivity; no motion command was sent",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读检查 AGV 与 JAKA 端口")
    parser.add_argument("--agv", default="192.168.192.5")
    parser.add_argument("--jaka", default="192.168.192.160")
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)
    report = read_only_device_report(args.agv, args.jaka, args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["agv"]["ok"] and report["jaka"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
