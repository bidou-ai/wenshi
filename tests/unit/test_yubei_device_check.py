from yubei.device_check import read_only_device_report


def test_read_only_device_report_uses_only_probes(monkeypatch):
    calls = []

    def fake_probe(host, port, timeout_s):
        calls.append((host, port, timeout_s))
        return {"host": host, "port": port, "ok": True, "rtt_ms": 1.0}

    monkeypatch.setattr("yubei.device_check.probe_tcp", fake_probe)
    report = read_only_device_report("agv", "jaka", timeout_s=0.2)
    assert report["agv"]["ok"] is True
    assert report["jaka"]["ok"] is True
    assert calls == [("agv", 19204, 0.2), ("jaka", 10001, 0.2)]
    assert "reverse" not in report
