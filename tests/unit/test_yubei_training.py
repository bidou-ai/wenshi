import yubei.train_yolo as train_yolo


def test_training_main_reports_actionable_dependency_error(monkeypatch, tmp_path, capsys):
    def fail(_args):
        raise RuntimeError("缺少 ultralytics；请先安装训练依赖")

    monkeypatch.setattr(train_yolo, "run_training", fail)

    result = train_yolo.main(["--data", str(tmp_path / "data.yaml")])

    assert result == 2
    assert "缺少 ultralytics" in capsys.readouterr().err
