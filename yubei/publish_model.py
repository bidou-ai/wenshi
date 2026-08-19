"""Explicitly publish a validated Ultralytics model to formal Wenshi models."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

try:
    from .paths import save_json_atomic
except ImportError:  # direct module execution
    from paths import save_json_atomic


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_model(source: Path, formal_models_dir: Path, metadata: dict) -> Path:
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() != ".pt":
        raise ValueError("model source must be a .pt file")
    if not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("model source does not exist or is empty")
    output_dir = Path(formal_models_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "rice_demo.pt"
    if output.exists():
        archive = output_dir / "archive"
        archive.mkdir(exist_ok=True)
        shutil.copy2(output, archive / f"{output.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        sidecar = output.with_suffix(".json")
        if sidecar.exists():
            shutil.copy2(sidecar, archive / f"{sidecar.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(source, output)
    value = dict(metadata)
    value.update(
        {
            "model": output.name,
            "classes": {"rice": 0, "flower": 1},
            "sha256": _sha256(output),
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    save_json_atomic(output.with_suffix(".json"), value)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="显式发布 YOLO 模型到 Wenshi/models")
    parser.add_argument("source", type=Path)
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--source-run", default="")
    args = parser.parse_args(argv)
    output = publish_model(args.source, args.models, {"source_run": args.source_run})
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
