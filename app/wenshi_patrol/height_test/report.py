"""Deterministic CSV/HTML/text reports for field review."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import html
import json
import math
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
    expected_views = ("left", "center", "right")
    evidence_views_seen = False
    for plant_dir in sorted((root / "plants").glob("*")) if (root / "plants").is_dir() else []:
        result = _json(plant_dir / "results.json")
        if not result:
            continue
        candidate = result.get("candidate_value_m")
        manual = _json(plant_dir / "manual.json")
        measured = manual.get("measured_height_m", manual.get("height_m"))
        measured = float(measured) if isinstance(measured, (int, float)) else None
        manual_error = abs(float(candidate) - measured) if isinstance(candidate, (int, float)) and measured is not None else None
        if isinstance(candidate, (int, float)):
            heights.append(float(candidate))
            if manual_error is not None:
                manual_errors.append(manual_error)
        view_root = plant_dir / "views"
        view_dirs = {path.name: path for path in view_root.iterdir() if path.is_dir()} if view_root.is_dir() else {}
        if set(view_dirs) != set(expected_views):
            camera_ok = False
            plant_model_ok = False
            panicle_model_ok = False
        sequences: list[int] = []
        for view_name in expected_views:
            view_dir = view_dirs.get(view_name)
            if view_dir is None:
                continue
            evidence_views_seen = True
            frame = _json(view_dir / "frame.json")
            detections = _json(view_dir / "detections.json")
            if not frame or not (view_dir / "color.jpg").is_file() or not (view_dir / "depth.png").is_file():
                camera_ok = False
            ratio = frame.get("depth_valid_ratio")
            if not isinstance(ratio, (int, float)) or float(ratio) < 0.70:
                camera_ok = False
            seq = frame.get("seq")
            if isinstance(seq, int):
                sequences.append(seq)
            else:
                camera_ok = False
            color_shape, depth_shape = frame.get("color_shape"), frame.get("depth_shape")
            if color_shape is not None and depth_shape is not None and list(color_shape[:2]) != list(depth_shape[:2]):
                camera_ok = False
            analysis = detections.get("analysis", {}) if isinstance(detections.get("analysis"), dict) else {}
            metadata = analysis.get("metadata", {}) if isinstance(analysis.get("metadata"), dict) else {}
            if not isinstance(metadata.get("selected_plant_box"), dict):
                plant_model_ok = False
            if not isinstance(metadata.get("panicle_count"), int) or int(metadata["panicle_count"]) < 1:
                panicle_model_ok = False
        if len(sequences) != len(expected_views) or any(current <= previous for previous, current in zip(sequences, sequences[1:])):
            camera_ok = False
        row = {
            "plant_id": result.get("plant_id", plant_dir.name),
            "candidate_method": result.get("candidate_method"),
            "height_m": candidate,
            "manual_height_m": measured,
            "manual_error_m": manual_error,
            "quality": result.get("quality", "needs_review"),
            "reasons": ";".join(result.get("reasons", [])),
        }
        plant_rows.append(row)
    feasible = (
        bool(heights)
        and sum(row["quality"] == "ok" for row in plant_rows) >= max(1, math.ceil(len(plant_rows) * 0.75))
        and len(manual_errors) >= 8
        and median(manual_errors) <= 0.020
    )
    summary = ReportSummary(
        flags={"camera_pass": camera_ok and evidence_views_seen and bool(plant_rows), "plant_model_pass": plant_model_ok and evidence_views_seen and bool(plant_rows), "panicle_model_pass": panicle_model_ok and evidence_views_seen and bool(plant_rows), "height_feasibility_pass": feasible},
        plants=tuple(plant_rows),
        metrics={"plant_count": len(plant_rows), "height_median": median(heights) if heights else None, "manual_pair_count": len(manual_errors), "manual_error_median_m": median(manual_errors) if manual_errors else None, "manual_error_max_m": max(manual_errors) if manual_errors else None},
    )
    return summary


def write_report(run_dir: Path, summary: ReportSummary | None = None) -> ReportSummary:
    root = Path(run_dir).expanduser().resolve()
    summary = summary or build_report(root)
    (root / "summary.json").write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (root / "report.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("plant_id", "candidate_method", "height_m", "manual_height_m", "manual_error_m", "quality", "reasons"))
        writer.writeheader(); writer.writerows(summary.plants)
    rows = "".join(
        f"<tr><td>{html.escape(str(row['plant_id']))}</td><td>{html.escape(str(row['candidate_method']))}</td><td>{html.escape(str(row['height_m']))}</td><td>{html.escape(str(row['manual_height_m']))}</td><td>{html.escape(str(row['manual_error_m']))}</td><td>{html.escape(str(row['quality']))}</td><td>{html.escape(str(row['reasons']))}</td></tr>"
        for row in summary.plants
    )
    flags = " ".join(f"<li>{html.escape(name)}: {'PASS' if value else 'FAIL'}</li>" for name, value in summary.flags.items())
    metrics = " ".join(f"<li>{html.escape(name)}: {html.escape(str(value))}</li>" for name, value in summary.metrics.items())
    document = f"<!doctype html><meta charset='utf-8'><title>Height test report</title><h1>Height test report</h1><h2>Flags</h2><ul>{flags}</ul><h2>Metrics</h2><ul>{metrics}</ul><table border='1'><tr><th>plant</th><th>method</th><th>height_m</th><th>manual_height_m</th><th>manual_error_m</th><th>quality</th><th>reasons</th></tr>{rows}</table>"
    (root / "report.html").write_text(document, encoding="utf-8")
    (root / "report.txt").write_text("\n".join(["height test report", *[f"{key}: {'PASS' if value else 'FAIL'}" for key, value in summary.flags.items()], *[f"{key}: {value}" for key, value in summary.metrics.items()], "", *[f"{row['plant_id']}: auto={row['height_m']} m manual={row['manual_height_m']} m error={row['manual_error_m']} m [{row['quality']}] {row['reasons']}" for row in summary.plants]]) + "\n", encoding="utf-8")
    return summary


__all__ = ["ReportSummary", "build_report", "write_report"]
