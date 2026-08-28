"""Read-only index for phenotyping runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import re


VIEWS = ("left", "center", "right")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SAFE_MEDIA = {"color.jpg", "depth.png", "frame.json"}


def _validate_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def _read_object(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else dict(default)
    return value if isinstance(value, dict) else ({} if default is None else dict(default))


class PhenotypeIndex:
    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root).expanduser().resolve()

    def _run_path(self, run_id: str) -> Path:
        try:
            _validate_component(run_id, "run id")
        except ValueError as exc:
            raise FileNotFoundError(run_id) from exc
        if not run_id.startswith("run_"):
            raise FileNotFoundError(run_id)
        path = (self.root / run_id).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(run_id) from exc
        if not self._is_phenotype_run(path):
            raise FileNotFoundError(run_id)
        return path

    @staticmethod
    def _is_phenotype_run(path: Path) -> bool:
        """Require the independent phenotyping run layout, not just ``run_*``."""
        if path.is_symlink() or not path.is_dir():
            return False
        run = _read_object(path / "run.json")
        plants = path / "plants"
        return (
            run.get("run_id") == path.name
            and isinstance(run.get("config_snapshot"), dict)
            and plants.is_dir()
            and not plants.is_symlink()
            and (path / "events.jsonl").is_file()
        )

    def _plant_path(self, run_id: str, plant_id: str) -> Path:
        run = self._run_path(run_id)
        try:
            _validate_component(plant_id, "plant id")
        except ValueError as exc:
            raise FileNotFoundError(plant_id) from exc
        plants = (run / "plants").resolve()
        unresolved = run / "plants" / plant_id
        if unresolved.is_symlink():
            raise FileNotFoundError(plant_id)
        path = unresolved.resolve()
        try:
            path.relative_to(plants)
        except ValueError as exc:
            raise FileNotFoundError(plant_id) from exc
        if path.name != plant_id or not path.is_dir() or not (path / "plant.json").is_file():
            raise FileNotFoundError(plant_id)
        return path

    def resolve_media(self, run_id: str, plant_id: str, view: str, filename: str) -> Path:
        plant = self._plant_path(run_id, plant_id)
        try:
            _validate_component(plant_id, "plant id")
            _validate_component(view, "capture view")
        except ValueError as exc:
            raise FileNotFoundError(filename) from exc
        if view not in VIEWS or filename not in _SAFE_MEDIA:
            raise ValueError("invalid phenotype media path")
        media = (plant / "captures" / view / filename).resolve()
        try:
            media.relative_to(plant)
        except ValueError as exc:
            raise ValueError("phenotype media path outside runtime root") from exc
        if not media.is_file():
            raise FileNotFoundError(filename)
        return media

    @staticmethod
    def _plant(path: Path, run_id: str) -> dict[str, Any]:
        value = _read_object(path / "plant.json", {"plant_id": path.name})
        value["run_id"] = run_id
        value["plant_id"] = path.name
        captures = dict(value.get("captures") or {})
        capture_values: dict[str, Any] = {}
        for view in VIEWS:
            record = captures.get(view)
            if not isinstance(record, dict):
                capture_values[view] = None
                continue
            item = dict(record)
            frame_path = path / "captures" / view / "frame.json"
            if frame_path.is_file():
                item["frame"] = _read_object(frame_path)
            capture_values[view] = item
        value["captures"] = capture_values
        traits = {}
        traits_dir = path / "traits"
        for name in ("plant_height", "effective_panicle_count"):
            traits[name] = _read_object(traits_dir / f"{name}.json")
        value["traits"] = traits
        review = _read_object(path / "review.json")
        if not review:
            review = {"state": value.get("review_status", "pending")}
        review.setdefault("state", review.get("status", value.get("review_status", "pending")))
        value["review"] = review
        return value

    @staticmethod
    def _group_status(plants: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for plant in plants:
            group_id = plant.get("observation_group")
            if group_id:
                groups.setdefault(str(group_id), []).append(plant)
        result = []
        for group_id in sorted(groups):
            items = groups[group_id]
            result.append({
                "group_id": group_id,
                "plant_count": len(items),
                "captured_plant_count": sum(not item.get("missing_views") and item.get("status") == "complete" for item in items),
                "reviewed_plant_count": sum((item.get("review") or {}).get("state", (item.get("review") or {}).get("status")) == "reviewed" for item in items),
                "plant_ids": [item["plant_id"] for item in items],
            })
        return result

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        values = []
        for path in self.root.iterdir():
            if not path.name.startswith("run_") or not self._is_phenotype_run(path):
                continue
            plants = self._plants(path)
            groups = self._group_status(plants)
            run = _read_object(path / "run.json", {"run_id": path.name, "status": "unknown"})
            run.update({
                "run_id": path.name,
                "plant_count": len(plants),
                "captured_plant_count": sum(item.get("status") == "complete" and not item.get("missing_views") for item in plants),
                "reviewed_plant_count": sum((item.get("review") or {}).get("state", (item.get("review") or {}).get("status")) == "reviewed" for item in plants),
                "observation_group_count": len(groups),
                "observation_groups": groups,
            })
            values.append(run)
        return sorted(values, key=lambda item: item["run_id"], reverse=True)

    @staticmethod
    def _plants(run_path: Path, run_id: str | None = None) -> list[dict[str, Any]]:
        actual_id = run_id or run_path.name
        directory = run_path / "plants"
        if not directory.is_dir():
            return []
        return [
            PhenotypeIndex._plant(path, actual_id)
            for path in sorted(directory.iterdir())
            if not path.is_symlink() and path.is_dir() and (path / "plant.json").is_file()
        ]

    def load_run(self, run_id: str) -> dict[str, Any]:
        path = self._run_path(run_id)
        value = _read_object(path / "run.json", {"run_id": run_id})
        plants = self._plants(path, run_id)
        value.update({
            "run_id": run_id,
            "plants": plants,
            "plant_count": len(plants),
            "captured_plant_count": sum(item.get("status") == "complete" and not item.get("missing_views") for item in plants),
            "reviewed_plant_count": sum((item.get("review") or {}).get("state", (item.get("review") or {}).get("status")) == "reviewed" for item in plants),
            "observation_groups": self._group_status(plants),
        })
        return value

    def load_plant(self, run_id: str, plant_id: str) -> dict[str, Any]:
        return self._plant(self._plant_path(run_id, plant_id), run_id)
