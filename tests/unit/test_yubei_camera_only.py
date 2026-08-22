from pathlib import Path

import yubei.camera_only as camera_only


def test_camera_only_check_ignores_agv_and_jaka(tmp_path, monkeypatch):
    config = tmp_path / "wenshi.yaml"
    config.write_text(
        "camera:\n  server_url: http://10.0.0.203:18080\n",
        encoding="utf-8",
    )
    seen = {}

    def fake_probe(url, samples, timeout_s):
        seen.update(url=url, samples=samples, timeout=timeout_s)
        return {"ok": True, "ok_samples": samples}

    monkeypatch.setattr(camera_only, "probe_camera", fake_probe)

    result = camera_only.run_check(config, samples=7)

    assert result["ok"] is True
    assert seen == {"url": "http://10.0.0.203:18080", "samples": 7, "timeout": 2.0}
