import pytest


def test_report_exposes_four_layer_flags(tmp_path):
    from wenshi_patrol.height_test.report import build_report, write_report
    (tmp_path / "plants").mkdir()
    summary = build_report(tmp_path)
    assert set(summary.flags) == {"camera_pass", "plant_model_pass", "panicle_model_pass", "height_feasibility_pass"}
    assert summary.flags["plant_model_pass"] is False
    assert summary.flags["panicle_model_pass"] is False


def test_height_feasibility_requires_eight_manual_pairs(tmp_path):
    import json

    from wenshi_patrol.height_test.report import build_report, write_report

    for index in range(8):
        plant = tmp_path / "plants" / f"A-{index + 1:02d}"
        plant.mkdir(parents=True)
        (plant / "results.json").write_text(
            json.dumps({"plant_id": plant.name, "candidate_value_m": 1.0, "candidate_method": "depth_roi_envelope", "quality": "ok", "reasons": []}),
            encoding="utf-8",
        )
    assert build_report(tmp_path).flags["height_feasibility_pass"] is False

    for plant in (tmp_path / "plants").iterdir():
        (plant / "manual.json").write_text(json.dumps({"measured_height_m": 1.01}), encoding="utf-8")
    summary = build_report(tmp_path)
    assert summary.flags["height_feasibility_pass"] is True
    assert summary.metrics["manual_pair_count"] == 8
    assert summary.plants[0]["manual_height_m"] == 1.01
    assert summary.plants[0]["manual_error_m"] == pytest.approx(0.01)
    write_report(tmp_path, summary)
    assert "manual_pair_count" in (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "manual_error_m" in (tmp_path / "report.csv").read_text(encoding="utf-8")


def test_report_accepts_shared_frame_sequences_across_two_plants(tmp_path):
    import json

    from wenshi_patrol.height_test.report import build_report

    for plant_id in ("A-01", "B-L-01"):
        plant = tmp_path / "plants" / plant_id
        plant.mkdir(parents=True)
        (plant / "results.json").write_text(
            json.dumps({"plant_id": plant_id, "candidate_value_m": 1.0, "candidate_method": "depth_roi_envelope", "quality": "ok", "reasons": []}),
            encoding="utf-8",
        )
        for seq, view in enumerate(("left", "center", "right"), start=1):
            target = plant / "views" / view
            target.mkdir(parents=True)
            (target / "color.jpg").touch()
            (target / "depth.png").touch()
            (target / "frame.json").write_text(
                json.dumps({"seq": seq, "depth_valid_ratio": 0.9, "color_shape": [20, 20, 3], "depth_shape": [20, 20]}),
                encoding="utf-8",
            )
            (target / "detections.json").write_text(
                json.dumps({"plant": [{"cx": 5}], "panicle": [{"cx": 5}], "analysis": {"metadata": {"selected_plant_box": {"cx": 5}, "panicle_count": 1}}}),
                encoding="utf-8",
            )
    summary = build_report(tmp_path)
    assert summary.flags["camera_pass"] is True
    assert summary.flags["plant_model_pass"] is True
    assert summary.flags["panicle_model_pass"] is True


def test_report_rejects_global_detection_not_assigned_to_current_plant(tmp_path):
    import json

    from wenshi_patrol.height_test.report import build_report

    plant = tmp_path / "plants" / "A-01"
    plant.mkdir(parents=True)
    (plant / "results.json").write_text(json.dumps({"plant_id": "A-01", "quality": "needs_review"}), encoding="utf-8")
    for seq, view in enumerate(("left", "center", "right"), start=1):
        target = plant / "views" / view
        target.mkdir(parents=True)
        (target / "color.jpg").touch()
        (target / "depth.png").touch()
        (target / "frame.json").write_text(json.dumps({"seq": seq, "depth_valid_ratio": 0.9}), encoding="utf-8")
        (target / "detections.json").write_text(
            json.dumps({"plant": [{"cx": 15}], "panicle": [{"cx": 15}], "analysis": {"metadata": {"selected_plant_box": None, "panicle_count": 0}}}),
            encoding="utf-8",
        )
    summary = build_report(tmp_path)
    assert summary.flags["plant_model_pass"] is False
    assert summary.flags["panicle_model_pass"] is False


def test_height_feasibility_rounds_required_quality_count_up(tmp_path):
    import json

    from wenshi_patrol.height_test.report import build_report

    for index in range(9):
        plant = tmp_path / "plants" / f"A-{index + 1:02d}"
        plant.mkdir(parents=True)
        quality = "ok" if index < 6 else "needs_review"
        (plant / "results.json").write_text(
            json.dumps({"plant_id": plant.name, "candidate_value_m": 1.0, "quality": quality, "reasons": []}),
            encoding="utf-8",
        )
        if index < 8:
            (plant / "manual.json").write_text(json.dumps({"measured_height_m": 1.0}), encoding="utf-8")
    assert build_report(tmp_path).flags["height_feasibility_pass"] is False
