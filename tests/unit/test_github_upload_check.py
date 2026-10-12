import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_github_upload.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_checker_lists_tracked_and_untracked_files_but_skips_ignored_files(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("noise\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "source.py")

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(tmp_path), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [item["path"] for item in report["files"]] == [
        ".gitignore",
        "config.yaml",
        "source.py",
    ]
    assert report["ok"] is True


def test_checker_rejects_oversized_files_and_suspected_secrets(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "large.bin").write_bytes(b"")
    (tmp_path / "large.bin").open("r+b").truncate(100 * 1024 * 1024 + 1)
    (tmp_path / ".env").write_text("MODE=production\n", encoding="utf-8")
    token = "gh" + "p_" + "A" * 36
    (tmp_path / "notes.txt").write_text(f"token={token}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(tmp_path), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert {(item["kind"], item["path"]) for item in report["errors"]} == {
        ("oversized_file", "large.bin"),
        ("sensitive_path", ".env"),
        ("suspected_secret", "notes.txt"),
    }
