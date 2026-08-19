"""Safe read-only index over Wenshi runtime runs."""

from __future__ import annotations

import json
from pathlib import Path


def _read(path: Path, fallback=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return value


class MediaResolver:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def resolve(self, run_id: str, target_id: str, filename: str) -> Path:
        if any(value in {"", ".", ".."} or "/" in value or "\\" in value for value in (run_id, target_id, filename)):
            raise ValueError("invalid media path")
        path = (self.root / run_id / "targets" / target_id / filename).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("media path outside runtime root") from exc
        if not path.is_file():
            raise FileNotFoundError(path)
        return path


class RunIndex:
    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root).expanduser().resolve()
        self.resolver = MediaResolver(self.root)

    def list_runs(self) -> list[dict]:
        values = []
        if not self.root.is_dir():
            return values
        for path in self.root.iterdir():
            if not path.is_dir() or not path.name.startswith("run_"):
                continue
            run = _read(path / "run.json", {"run_id": path.name, "status": "unknown"})
            run["run_id"] = path.name
            run["target_count"] = sum(1 for target in (path / "targets").iterdir() if target.is_dir() and target.name.startswith("T")) if (path / "targets").is_dir() else 0
            values.append(run)
        return sorted(values, key=lambda value: value["run_id"], reverse=True)

    def load_run(self, run_id: str) -> dict:
        path = self.root / run_id
        if not run_id.startswith("run_") or not path.is_dir():
            raise FileNotFoundError(run_id)
        run = _read(path / "run.json", {"run_id": run_id})
        run["run_id"] = run_id
        run["targets"] = [self.load_target(run_id, target.name) for target in sorted((path / "targets").iterdir()) if target.is_dir() and target.name.startswith("T")] if (path / "targets").is_dir() else []
        return run

    def load_target(self, run_id: str, target_id: str) -> dict:
        if not run_id.startswith("run_") or not target_id.startswith("T"):
            raise FileNotFoundError(target_id)
        value = _read(self.root / run_id / "targets" / target_id / "metadata.json", None)
        if not isinstance(value, dict):
            raise FileNotFoundError(target_id)
        value["target_id"] = target_id
        value["run_id"] = run_id
        return value
