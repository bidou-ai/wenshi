"""Deterministic CSV/HTML/text reports for field review."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import html
import json
from pathlib import Path
from statistics import median
from typing import Any


@dataclass(frozen=True)
class ReportSummary:
    flags: dict[str, bool]
    plants: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"flags": dict(self.flags), "plants": list(self.plants), "metrics": dict(self.metrics)}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_report(run_dir: Path) -> ReportSummary:
    root = Path(run_dir).expanduser().resolve()
    plant_rows: list[dict[str, Any]] = []
    camera_ok = True
    plant_model_ok = True
    panicle_model_ok = True
    heights: list[float] = []
    manual_errors: list[float] = []
    for plant_dir in sorted((root / "plants").glob("*")) if (root / "plants").is_dir() else []:
        result = _json(plant_dir / "results.json")
        if not result:
            continue
        candidate = result.get("candidate_value_m")
        if isinstance(candidate, (int, float)):
            heights.append(float(candidate))
            manual = _json(plant_dir / "manual.json")
            measured = manual.get("measured_height_m", manual.get("height_m"))
            if isinstance(measured, (int, float)):
                manual_errors.append(abs(float(candidate) - float(measured)))
        for view_dir in sorted((plant_dir / "views").glob("*")) if (plant_dir / "views").is_dir() else []:
            frame = _json(view_dir / "frame.json")
            detections = _json(view_dir / "detections.json")
            if not frame or not (view_dir / "color.jpg").is_file() or not (view_dir / "depth.png").is_file():
                camera_ok = False
            if not detections.get("plant"):
                plant_model_ok = False
            if not detections.get("panicle"):
                panicle_model_ok = False
        row = {
            "plant_id": result.get("plant_id", plant_dir.name),
            "candidate_method": result.get("candidate_method"),
            "height_m": candidate,
            "quality": result.get("quality", "needs_review"),
            "reasons": ";".join(result.get("reasons", [])),
        }
        plant_rows.append(row)
    feasible = bool(heights) and sum(row["quality"] == "ok" for row in plant_rows) >= max(1, int(len(plant_rows) * 0.75))
    if manual_errors and median(manual_errors) > 0.020:
        feasible = False
    summary = ReportSummary(
        flags={"camera_pass": camera_ok and bool(plant_rows), "plant_model_pass": plant_model_ok, "panicle_model_pass": panicle_model_ok, "height_feasibility_pass": feasible},
        plants=tuple(plant_rows),
        metrics={"plant_count": len(plant_rows), "height_median": median(heights) if heights else None, "manual_error_median_m": median(manual_errors) if manual_errors else None, "manual_error_max_m": max(manual_errors) if manual_errors else None},
    )
    return summary


def write_report(run_dir: Path, summary: ReportSummary | None = None) -> ReportSummary:
    root = Path(run_dir).expanduser().resolve()
    summary = summary or build_report(root)
    (root / "summary.json").write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (root / "report.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("plant_id", "candidate_method", "height_m", "quality", "reasons"))
        writer.writeheader(); writer.writerows(summary.plants)
    rows = "".join(
        f"<tr><td>{html.escape(str(row['plant_id']))}</td><td>{html.escape(str(row['candidate_method']))}</td><td>{html.escape(str(row['height_m']))}</td><td>{html.escape(str(row['quality']))}</td><td>{html.escape(str(row['reasons']))}</td></tr>"
        for row in summary.plants
    )
    flags = " ".join(f"<li>{html.escape(name)}: {'PASS' if value else 'FAIL'}</li>" for name, value in summary.flags.items())
    document = f"<!doctype html><meta charset='utf-8'><title>Height test report</title><h1>Height test report</h1><ul>{flags}</ul><table border='1'><tr><th>plant</th><th>method</th><th>height_m</th><th>quality</th><th>reasons</th></tr>{rows}</table>"
    (root / "report.html").write_text(document, encoding="utf-8")
    (root / "report.txt").write_text("\n".join(["height test report", *[f"{key}: {'PASS' if value else 'FAIL'}" for key, value in summary.flags.items()], "", *[f"{row['plant_id']}: {row['height_m']} m [{row['quality']}] {row['reasons']}" for row in summary.plants]]) + "\n", encoding="utf-8")
    return summary


__all__ = ["ReportSummary", "build_report", "write_report"]
