"""Offline quality and duplicate report for one captured dataset session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2

try:
    from .capture_quality import assess_image, image_signature, signature_distance
    from .paths import load_json, save_json_atomic
except ImportError:  # direct module execution
    from capture_quality import assess_image, image_signature, signature_distance
    from paths import load_json, save_json_atomic


def audit_session(session: Path) -> dict[str, Any]:
    root = Path(session).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {"images": []}
    entries = list(manifest.get("images", []))
    report: dict[str, Any] = {
        "session": str(root),
        "images": 0,
        "tags": {"flower": 0, "rice": 0, "neutral": 0},
        "quality_warnings": 0,
        "duplicates": 0,
        "items": [],
    }
    signatures: list[tuple[int, str]] = []
    for entry in entries:
        relative = str(entry.get("filename", ""))
        if not relative.startswith("images/"):
            continue
        image_path = root / relative
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            report["quality_warnings"] += 1
            report["items"].append({"filename": relative, "error": "decode_failed"})
            continue
        quality = assess_image(image)
        signature = image_signature(image)
        duplicate_of = ""
        for previous, previous_name in reversed(signatures):
            if signature_distance(signature, previous) <= 3:
                duplicate_of = previous_name
                break
        if not quality["ok"] or duplicate_of:
            report["quality_warnings"] += 1
        if duplicate_of:
            report["duplicates"] += 1
        tag = str(entry.get("capture_tag", "neutral"))
        report["tags"][tag] = report["tags"].get(tag, 0) + 1
        report["items"].append({
            "filename": relative,
            "capture_tag": tag,
            "quality": quality,
            "duplicate_of": duplicate_of,
        })
        signatures.append((signature, relative))
    report["images"] = len(report["items"])
    save_json_atomic(root / "capture_audit.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查数据集照片质量、开花批次和重复图")
    parser.add_argument("session", type=Path)
    args = parser.parse_args(argv)
    report = audit_session(args.session)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
