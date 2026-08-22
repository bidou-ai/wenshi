from pathlib import Path

import yubei.check_all as check_all


def test_check_all_reads_device_and_camera_endpoints_from_config(tmp_path, monkeypatch):
    config = tmp_path / "wenshi.yaml"
    config.write_text(
        """
agv:
  ip: 10.0.0.5
  status_port: 19204
jaka:
  ip: 10.0.0.160
camera:
  server_url: http://10.0.0.203:18080
""",
        encoding="utf-8",
    )
    seen = {}

    monkeypatch.setattr(check_all, "probe_tcp", lambda host, port, timeout: seen.setdefault("tcp", []).append((host, port, timeout)) or type("R", (), {"to_dict": lambda self: {"ok": True}})())
    def fake_camera(url, samples, timeout_s):
        seen["camera"] = (url, samples, timeout_s)
        return {"ok": True}

    monkeypatch.setattr(check_all, "probe_camera", fake_camera)
    monkeypatch.setattr(check_all, "default_route", lambda: "10.0.0.2")

    result = check_all.run_checks(config, samples=2)

    assert result["ok"] is True
    assert ("10.0.0.5", 19204, 1.0) in seen["tcp"]
    assert ("10.0.0.5", 19205, 1.0) in seen["tcp"]
    assert ("10.0.0.160", 10001, 1.0) in seen["tcp"]
    assert seen["camera"] == ("http://10.0.0.203:18080", 2, 2.0)
