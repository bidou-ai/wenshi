from pathlib import Path
import subprocess


def test_wenshi_shell_has_dry_run_and_demo_commands():
    path = Path("wenshi.sh")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "--dry-run" in text
    assert "camera_bridge" in text
    assert "rviz2" in text
    assert "runtime/demo" in text


def test_wenshi_dry_run_does_not_open_hardware():
    result = subprocess.run(["bash", "wenshi.sh", "--dry-run"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "16" in result.stdout + result.stderr
