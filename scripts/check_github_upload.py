#!/usr/bin/env python3
"""Inspect files eligible for the next GitHub upload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


GITHUB_FILE_LIMIT = 100 * 1024 * 1024
SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
SECRET_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
MAX_SECRET_SCAN_BYTES = 10 * 1024 * 1024


def _git_files(root: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(path.decode("utf-8") for path in result.stdout.split(b"\0") if path)


def _is_sensitive_path(path: Path) -> bool:
    return path.name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES


def _contains_suspected_secret(path: Path, size: int) -> bool:
    if size > MAX_SECRET_SCAN_BYTES:
        return False
    try:
        content = path.read_bytes()
    except OSError:
        return False
    return any(pattern.search(content) for pattern in SECRET_PATTERNS)


def build_report(root: Path) -> dict[str, object]:
    files = []
    errors = []
    total_bytes = 0
    for relative in _git_files(root):
        path = root / relative
        if not path.is_file():
            continue
        size = path.stat().st_size
        total_bytes += size
        files.append({"path": relative, "size_bytes": size})
        if size > GITHUB_FILE_LIMIT:
            errors.append({"kind": "oversized_file", "path": relative})
        if _is_sensitive_path(path):
            errors.append({"kind": "sensitive_path", "path": relative})
        elif _contains_suspected_secret(path, size):
            errors.append({"kind": "suspected_secret", "path": relative})
    return {
        "ok": not errors,
        "total_bytes": total_bytes,
        "files": files,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(args.root.resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"可上传文件：{len(report['files'])}")
        print(f"总大小：{report['total_bytes']} bytes")
        for error in report["errors"]:
            print(f"错误：{error['kind']}: {error['path']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
