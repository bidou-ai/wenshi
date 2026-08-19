"""D435 HTTP health, frame, decode and optional live preview diagnostic."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import cv2

try:
    from .http_camera import HttpCameraClient
except ImportError:  # direct `python yubei/camera_check.py`
    from http_camera import HttpCameraClient


def probe_camera(url: str, samples: int = 10, interval_s: float = 0.2, timeout_s: float = 2.0, client=None) -> dict[str, Any]:
    camera = client or HttpCameraClient(url, timeout_s=timeout_s)
    report: dict[str, Any] = {"url": url, "health": None, "samples": [], "seq_gaps": 0}
    try:
        report["health"] = camera.health()
    except Exception as exc:
        report["health_error"] = str(exc)
    last_seq = None
    for index in range(max(int(samples), 0)):
        started = time.perf_counter()
        try:
            frame = camera.frame()
            if last_seq is not None and frame.seq > last_seq + 1:
                report["seq_gaps"] += frame.seq - last_seq - 1
            report["samples"].append(
                {
                    "index": index,
                    "seq": frame.seq,
                    "color": list(frame.color.shape),
                    "depth": list(frame.depth.shape),
                    "depth_dtype": str(frame.depth.dtype),
                    "decode_ms": round((time.perf_counter() - started) * 1000.0, 2),
                }
            )
            last_seq = frame.seq
        except Exception as exc:
            report["samples"].append({"index": index, "error": str(exc)})
        if index + 1 < samples:
            time.sleep(max(float(interval_s), 0.0))
    report["ok_samples"] = sum("error" not in item for item in report["samples"])
    report["ok"] = bool(report["health"].get("ok")) if isinstance(report["health"], dict) else False
    report["ok"] = report["ok"] and report["ok_samples"] == len(report["samples"])
    return report


def preview(url: str, timeout_s: float) -> int:
    camera = HttpCameraClient(url, timeout_s=timeout_s)
    while True:
        try:
            frame = camera.frame()
        except Exception as exc:
            print(f"frame error: {exc}")
            time.sleep(0.2)
            continue
        depth = cv2.normalize(frame.depth, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")
        depth = cv2.applyColorMap(depth, cv2.COLORMAP_TURBO)
        cv2.putText(frame.color, f"seq={frame.seq}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("yubei RGB", frame.color)
        cv2.imshow("yubei depth", depth)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查 Windows D435 HTTP 服务")
    parser.add_argument("--url", default="http://192.168.192.203:18080")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(argv)
    if args.preview:
        return preview(args.url, args.timeout)
    print(json.dumps(probe_camera(args.url, args.samples, args.interval, args.timeout), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
