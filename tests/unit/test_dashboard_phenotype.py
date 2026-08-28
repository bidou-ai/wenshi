import csv
import io
import json
from pathlib import Path

import pytest

from dashboard.export import CSV_COLUMNS, export_csv
from dashboard.admin import AdminActions
from dashboard.phenotype_index import PhenotypeIndex
from dashboard.run_index import RunIndex
from dashboard.server import DashboardHandler, dashboard_host_policy, parse_phenotype_path


def _write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _make_run(root: Path, run_id: str = "run_20260828_120000") -> Path:
    run = root / run_id
    _write_json(
        run / "run.json",
        {
            "run_id": run_id,
            "status": "finished",
            "created_at": "2026-08-28T12:00:00Z",
            "config_snapshot": {"phenotyping": {"enabled": False}},
        },
    )
    (run / "events.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (run / "events.jsonl").write_text("", encoding="utf-8")
    for plant_id, tag_id, review_status in (
        ("A-01", 7, "reviewed"),
        ("B-L-01", None, "pending"),
    ):
        plant = run / "plants" / plant_id
        _write_json(
            plant / "plant.json",
            {
                "plant_id": plant_id,
                "tag_id": tag_id,
                "region": "A" if plant_id.startswith("A") else "B",
                "row": "row-1",
                "index": 1,
                "observation_group": "left-01",
                "status": "complete" if review_status == "reviewed" else "partial",
                "captures": {
                    "left": {"status": "captured", "directory": "captures/left"},
                    "center": None,
                    "right": None,
                },
            },
        )
        _write_json(plant / "review.json", {"state": review_status, "reasons": []})
        _write_json(
            plant / "traits" / "plant_height.json",
            {"auto_value_m": 0.82, "reviewed_value_m": 0.8, "difference_m": -0.02, "status": "reviewed"},
        )
        _write_json(
            plant / "traits" / "effective_panicle_count.json",
            {"automatic_value": 4, "reviewed_value": 5, "difference": 1, "status": "reviewed"},
        )
        _write_json(
            plant / "captures" / "left" / "frame.json",
            {"view": "left", "quality": {"score": 0.91}, "tag": {"tag_id": tag_id}},
        )
        (plant / "captures" / "left" / "color.jpg").write_bytes(b"jpeg")
        (plant / "captures" / "left" / "depth.png").write_bytes(b"png")
    return run


def test_index_lists_runs_with_plant_and_stop_status(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)

    values = PhenotypeIndex(root).list_runs()

    assert values[0]["run_id"] == run.name
    assert values[0]["plant_count"] == 2
    assert values[0]["captured_plant_count"] == 1
    assert values[0]["reviewed_plant_count"] == 1
    assert values[0]["observation_group_count"] == 1
    assert values[0]["observation_groups"][0]["plant_count"] == 2


def test_index_loads_run_and_plant_with_capture_and_trait_evidence(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)

    loaded = PhenotypeIndex(root).load_run(run.name)
    plant = PhenotypeIndex(root).load_plant(run.name, "A-01")

    assert [item["plant_id"] for item in loaded["plants"]] == ["A-01", "B-L-01"]
    assert loaded["captured_plant_count"] == 1
    assert loaded["reviewed_plant_count"] == 1
    assert plant["tag_id"] == 7
    assert plant["captures"]["left"]["frame"]["quality"]["score"] == 0.91
    assert plant["traits"]["plant_height"]["reviewed_value_m"] == 0.8
    assert plant["review"]["state"] == "reviewed"


def test_index_rejects_invalid_run_and_plant_paths(tmp_path):
    root = tmp_path / "runs"
    _make_run(root)
    index = PhenotypeIndex(root)

    with pytest.raises(FileNotFoundError):
        index.load_run("../run_20260828_120000")
    with pytest.raises(FileNotFoundError):
        index.load_plant("run_20260828_120000", "../../secret")


def test_index_excludes_legacy_patrol_run_without_phenotype_marker(tmp_path):
    root = tmp_path / "runs"
    _make_run(root)
    legacy = root / "run_20260827_120000"
    _write_json(legacy / "run.json", {"run_id": legacy.name, "status": "finished"})
    _write_json(legacy / "targets" / "T0001" / "metadata.json", {"target_id": "T0001"})

    assert [item["run_id"] for item in PhenotypeIndex(root).list_runs()] == ["run_20260828_120000"]
    with pytest.raises(FileNotFoundError):
        PhenotypeIndex(root).load_run(legacy.name)


def test_index_resolves_only_existing_plant_media_inside_run(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)
    media = run / "plants" / "A-01" / "captures" / "left" / "color.jpg"
    media.write_bytes(b"jpeg")
    index = PhenotypeIndex(root)

    assert index.resolve_media(run.name, "A-01", "left", "color.jpg") == media
    with pytest.raises((ValueError, FileNotFoundError)):
        index.resolve_media(run.name, "A-01", "left", "../../run.json")


def test_index_rejects_symlinked_plant_that_resolves_to_another_plant(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)
    alias = run / "plants" / "alias"
    alias.symlink_to(run / "plants" / "A-01", target_is_directory=True)

    with pytest.raises(FileNotFoundError):
        PhenotypeIndex(root).load_plant(run.name, "alias")
    with pytest.raises((ValueError, FileNotFoundError)):
        PhenotypeIndex(root).resolve_media(run.name, "alias", "left", "color.jpg")


def test_index_uses_plant_review_status_when_review_file_is_absent(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)
    review = run / "plants" / "A-01" / "review.json"
    review.unlink()
    plant_json = run / "plants" / "A-01" / "plant.json"
    value = json.loads(plant_json.read_text(encoding="utf-8"))
    value["review_status"] = "reviewed"
    _write_json(plant_json, value)

    plant = PhenotypeIndex(root).load_plant(run.name, "A-01")

    assert plant["review"]["state"] == "reviewed"


def test_csv_export_has_stable_columns_and_utf8_values(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)
    output = io.StringIO()

    export_csv(run, output)

    output.seek(0)
    rows = list(csv.DictReader(output))
    assert list(rows[0]) == CSV_COLUMNS
    assert rows[0]["run_id"] == run.name
    assert rows[0]["plant_id"] == "A-01"
    assert rows[0]["tag_id"] == "7"
    assert rows[0]["plant_height_reviewed_m"] == "0.8"
    assert rows[0]["effective_panicle_count_reviewed"] == "5"
    assert rows[0]["review_state"] == "reviewed"


def test_csv_export_rejects_non_run_directory(tmp_path):
    with pytest.raises(ValueError, match="run"):
        export_csv(tmp_path / "plants", io.StringIO())


def test_csv_media_links_include_the_capture_filename(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)
    output = io.StringIO()

    export_csv(run, output)

    output.seek(0)
    rows = list(csv.DictReader(output))
    links = rows[0]["media_links"]
    assert "/left/color.jpg" in links
    assert "/left/depth.png" in links
    assert "/left/frame.json" in links
    assert "/center/color.jpg" not in links


def test_csv_does_not_link_capture_files_missing_from_disk(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root)
    (run / "plants" / "A-01" / "captures" / "left" / "depth.png").unlink()
    output = io.StringIO()

    export_csv(run, output)

    row = next(csv.DictReader(io.StringIO(output.getvalue())))
    assert "/left/color.jpg" in row["media_links"]
    assert "/left/frame.json" in row["media_links"]
    assert "/left/depth.png" not in row["media_links"]


def test_non_loopback_dashboard_requires_a_pin_and_authenticated_phenotype_reads():
    with pytest.raises(ValueError, match="PIN"):
        dashboard_host_policy("0.0.0.0", "")
    assert dashboard_host_policy("127.0.0.1", "") is False
    assert dashboard_host_policy("192.168.1.20", "1234") is True


def test_phenotype_frontend_uses_only_phenotype_api_paths():
    script = (Path(__file__).resolve().parents[2] / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")

    assert '"/api/phenotype/runs"' in script
    assert '"/api/runs"' not in script
    assert '"/media/' not in script


def test_phenotype_frontend_downloads_csv_through_authenticated_fetch():
    script = (Path(__file__).resolve().parents[2] / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")

    assert "downloadExport" in script
    assert "X-Wenshi-Token" in script


def test_external_phenotype_reads_require_token_and_strict_paths(tmp_path):
    admin = AdminActions(tmp_path / "runs", "1234")
    handler = object.__new__(DashboardHandler)
    handler.phenotype_requires_auth = True
    handler.admin = admin
    handler.headers = {"X-Wenshi-Token": ""}

    with pytest.raises(PermissionError):
        handler._require_phenotype_auth()
    handler.headers = {"X-Wenshi-Token": admin.authenticate("1234")}
    handler._require_phenotype_auth()
    assert parse_phenotype_path("/api/phenotype/runs/run_20260828_120000") == ("run", "run_20260828_120000")
    assert parse_phenotype_path("/api/phenotype/runs/run_20260828_120000/plants/A-01/media/left/color.jpg") == ("media", "run_20260828_120000", "A-01", "left", "color.jpg")
    assert parse_phenotype_path("/api/phenotype/runs/run_20260828_120000/plants/A-01/extra") is None
