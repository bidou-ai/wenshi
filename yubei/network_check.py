"""Network reachability checks for the dedicated greenhouse LAN."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import socket
import time
from typing import Any


@dataclass
class ProbeResult:
    host: str
    port: int
    ok: bool
    rtt_ms: float | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_tcp(host: str, port: int, timeout_s: float) -> ProbeResult:
    started = time.perf_counter()
    try:
        with socket.create_connection((str(host), int(port)), timeout=max(float(timeout_s), 0.01)):
            pass
    except OSError as exc:
        return ProbeResult(str(host), int(port), False, None, str(exc))
    return ProbeResult(str(host), int(port), True, (time.perf_counter() - started) * 1000.0)


def default_route() -> str | None:
    """Return the local source address used for a public UDP route probe."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="检查 Wenshi 温室局域网连通性")
    parser.add_argument("--agv", default="192.168.192.5")
    parser.add_argument("--jaka", default="192.168.192.160")
    parser.add_argument("--camera", default="192.168.192.203")
    parser.add_argument("--camera-port", type=int, default=18080)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)
    result = {
        "source_ip": default_route(),
        "agv_status": probe_tcp(args.agv, 19204, args.timeout).to_dict(),
        "jaka": probe_tcp(args.jaka, 10001, args.timeout).to_dict(),
        "camera": probe_tcp(args.camera, args.camera_port, args.timeout).to_dict(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for key, item in result.items() if key != "source_ip") else 1


if __name__ == "__main__":
    raise SystemExit(main())
