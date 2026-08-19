"""Explicit, preview-first cleanup for historical patrol runs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil


@dataclass(frozen=True)
class CleanupPlan:
    root: Path
    run_ids: list[str]
    total_files: int
    total_bytes: int


@dataclass(frozen=True)
class CleanupResult:
    removed_runs: list[str]
    total_files: int
    total_bytes: int


def _run_path(root: Path, run_id: str) -> Path:
    if not run_id.startswith("run_") or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError("invalid run id")
    path = (root / run_id).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("run path outside runtime root") from exc
    if not path.is_dir():
        raise FileNotFoundError(run_id)
    value = json.loads((path / "run.json").read_text(encoding="utf-8")) if (path / "run.json").exists() else {}
    if value.get("status") == "running":
        raise ValueError("不能清理正在运行的巡检")
    return path


def preview_cleanup(runtime_root: Path, run_ids: list[str]) -> CleanupPlan:
    root = Path(runtime_root).expanduser().resolve()
    files = []
    for run_id in run_ids:
        path = _run_path(root, run_id)
        files.extend(item for item in path.rglob("*") if item.is_file())
    return CleanupPlan(root, list(run_ids), len(files), sum(item.stat().st_size for item in files))


def execute_cleanup(plan: CleanupPlan, confirm: str) -> CleanupResult:
    if len(plan.run_ids) != 1 or confirm != plan.run_ids[0]:
        raise ValueError("必须用完整 run_id 确认清理")
    path = _run_path(plan.root, plan.run_ids[0])
    shutil.rmtree(path)
    return CleanupResult(plan.run_ids, plan.total_files, plan.total_bytes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="预览并清理历史 Wenshi 巡检目录")
    parser.add_argument("--root", type=Path, default=Path("runtime/runs"))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--preview")
    parser.add_argument("--execute")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(path.name for path in args.root.glob("run_*") if path.is_dir()))
        return 0
    run_id = args.preview or args.execute
    if not run_id:
        parser.error("需要 --list、--preview 或 --execute")
    plan = preview_cleanup(args.root, [run_id])
    print(f"预览: {plan.run_ids} 文件={plan.total_files} bytes={plan.total_bytes}")
    if args.execute:
        print(execute_cleanup(plan, args.confirm or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
