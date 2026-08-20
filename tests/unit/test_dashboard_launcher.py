from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_launcher_opens_local_browser_and_uses_project_runtime():
    script = ROOT / "scripts" / "start_dashboard.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111
    text = script.read_text(encoding="utf-8")
    assert "127.0.0.1" in text
    assert "runtime/runs" in text
    assert "xdg-open" in text
    assert "dashboard/server.py" in text


def test_dashboard_launcher_does_not_bind_external_interfaces_by_default():
    text = (ROOT / "scripts" / "start_dashboard.sh").read_text(encoding="utf-8")
    assert "0.0.0.0" not in text
    assert "--host \"$HOST\"" in text


def test_dashboard_launcher_bypasses_http_proxy_for_local_health_check():
    text = (ROOT / "scripts" / "start_dashboard.sh").read_text(encoding="utf-8")
    assert "no_proxy" in text
    assert "NO_PROXY" in text
    assert "ProxyHandler({})" in text
