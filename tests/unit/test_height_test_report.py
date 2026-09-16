def test_report_exposes_four_layer_flags(tmp_path):
    from wenshi_patrol.height_test.report import build_report
    (tmp_path / "plants").mkdir()
    assert set(build_report(tmp_path).flags) == {"camera_pass", "plant_model_pass", "panicle_model_pass", "height_feasibility_pass"}
