"""Stable CSV export for phenotyping runs."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TextIO

try:
    from .phenotype_index import PhenotypeIndex, VIEWS
except ImportError:  # direct: python3 dashboard/server.py
    from phenotype_index import PhenotypeIndex, VIEWS

CSV_COLUMNS = [
    "run_id", "plant_id", "tag_id", "region", "row", "index", "observation_group", "status",
    "capture_left", "capture_center", "capture_right", "plant_height_auto_m", "plant_height_reviewed_m",
    "plant_height_difference_m", "effective_panicle_count_automatic", "effective_panicle_count_reviewed",
    "effective_panicle_count_difference", "quality", "review_state", "review_reasons", "media_links",
]


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def export_csv(run_dir: Path, output: TextIO) -> None:
    run_dir = Path(run_dir).expanduser().resolve()
    if not run_dir.name.startswith("run_"):
        raise ValueError("run directory is required")
    index = PhenotypeIndex(run_dir.parent)
    run = index.load_run(run_dir.name)
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for plant in run["plants"]:
        height = plant["traits"].get("plant_height", {})
        panicles = plant["traits"].get("effective_panicle_count", {})
        review = plant.get("review", {})
        links = []
        for view in VIEWS:
            for filename in ("color.jpg", "depth.png", "frame.json"):
                try:
                    index.resolve_media(run["run_id"], plant["plant_id"], view, filename)
                except (ValueError, FileNotFoundError):
                    continue
                links.append(f"/api/phenotype/runs/{run['run_id']}/plants/{plant['plant_id']}/media/{view}/{filename}")
        writer.writerow({
            "run_id": run["run_id"], "plant_id": plant.get("plant_id"), "tag_id": plant.get("tag_id"),
            "region": plant.get("region"), "row": plant.get("row"), "index": plant.get("index"),
            "observation_group": plant.get("observation_group"), "status": plant.get("status"),
            "capture_left": plant["captures"].get("left") is not None,
            "capture_center": plant["captures"].get("center") is not None,
            "capture_right": plant["captures"].get("right") is not None,
            "plant_height_auto_m": height.get("auto_value_m"), "plant_height_reviewed_m": height.get("reviewed_value_m"),
            "plant_height_difference_m": height.get("difference_m"),
            "effective_panicle_count_automatic": panicles.get("automatic_value"),
            "effective_panicle_count_reviewed": panicles.get("reviewed_value"),
            "effective_panicle_count_difference": panicles.get("difference"),
            "quality": _text(plant.get("quality")),
            "review_state": review.get("state", review.get("status", "pending")),
            "review_reasons": _text(review.get("reasons", [])), "media_links": ";".join(links),
        })
