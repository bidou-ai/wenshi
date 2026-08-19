from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_project_has_independent_runtime_boundaries():
    assert (PROJECT_ROOT / "app" / "wenshi_patrol").is_dir()
    assert (PROJECT_ROOT / "app" / "wenshi_patrol" / "route_controller.py").is_file()
    assert (PROJECT_ROOT / "config" / "wenshi.yaml").is_file()
    assert (PROJECT_ROOT / "map" / "wenshi.smap").is_file()
    assert (PROJECT_ROOT / "config" / "viewpoints.json").is_file()
    assert (PROJECT_ROOT / "calibration" / "README.md").is_file()
    assert (PROJECT_ROOT / "datasets" / "README.md").is_file()


def test_runtime_is_the_only_tracked_runtime_boundary():
    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "runtime/*" in ignored
    assert "!runtime/.gitkeep" in ignored
    assert not (PROJECT_ROOT / "logs").exists()
    assert not (PROJECT_ROOT / "build").exists()
    assert not (PROJECT_ROOT / "install").exists()


def test_project_does_not_depend_on_original_runtime_tree():
    source_root = PROJECT_ROOT / "app" / "wenshi_patrol"
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in source_root.rglob("*.py")
    )
    assert "rice_testing" not in source_text
    assert "rice_patrol_wens1" not in source_text


def test_project_has_no_historical_runtime_artifacts():
    forbidden = {"logs", "log", "build", "install", ".pytest_cache", "__pycache__"}
    ignored_names = {".git", ".pytest_cache", "__pycache__"}
    found = {
        path.name
        for path in PROJECT_ROOT.rglob("*")
        if path.is_dir()
        and path.name in forbidden
        and not any(root.name in ignored_names for root in (path, *path.parents))
    }
    assert not found
