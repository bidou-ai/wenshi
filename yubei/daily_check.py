"""Read-only daily health check for offline data and model workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import yaml


def _status(label: str, state: str, detail: str) -> None:
    print(f"[{state}] {label}: {detail}")


def _dataset_summary(root: Path, expected_type: str) -> tuple[str, bool]:
    if not root.is_dir():
        return "目录不存在", False
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return "缺少 manifest.json", False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"manifest 无法读取: {exc}", False
    actual_type = manifest.get("dataset_type")
    classes = manifest.get("classes")
    images = list((root / "images").glob("*")) if (root / "images").is_dir() else []
    labels = list((root / "labels").glob("*.txt")) if (root / "labels").is_dir() else []
    metadata = list((root / "labels").glob("*.json")) if (root / "labels").is_dir() else []
    statuses: dict[str, int] = {}
    invalid_status = False
    for path in metadata:
        try:
            status = str(json.loads(path.read_text(encoding="utf-8")).get("status", "unknown"))
        except (OSError, json.JSONDecodeError):
            status = "invalid"
        if status not in {"unlabelled", "labelled", "ambiguous", "skipped"}:
            invalid_status = True
        statuses[status] = statuses.get(status, 0) + 1
    ok = actual_type == expected_type and classes == ({"rice_plant": 0} if expected_type == "plant" else {"panicle": 0})
    detail = f"图片 {len(images)}，YOLO标签 {len(labels)}，标注状态 {statuses}"
    if not images:
        detail += "，没有图片"
        ok = False
    if actual_type != expected_type:
        detail += f"，类型应为 {expected_type}，实际为 {actual_type}"
    if invalid_status:
        detail += "，存在非法标注状态"
    ok = ok and not invalid_status
    return detail, ok


def _prepared_summary(root: Path, expected_type: str) -> tuple[str, bool]:
    if not root.is_dir():
        return "没有准备目录", False
    prepared = [path for path in root.glob("*/data.yaml") if path.is_file()]
    if not prepared:
        return "没有 data.yaml", False
    latest = max(prepared, key=lambda path: path.stat().st_mtime)
    try:
        value = yaml.safe_load(latest.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return f"data.yaml 无法读取: {exc}", False
    expected_name = "rice_plant" if expected_type == "plant" else "panicle"
    names = value.get("names", {})
    actual_names = set(names.values()) if isinstance(names, dict) else set(names or [])
    ok = actual_names == {expected_name}
    return f"最新 {latest.parent.name}，类别 {sorted(actual_names)}", ok


def _model_summary(root: Path, expected_type: str) -> tuple[str, bool]:
    expected_name = "rice_plant" if expected_type == "plant" else "panicle"
    models = list(root.glob(f"{expected_type}/*/weights/best.pt")) if root.is_dir() else []
    if not models:
        return "没有 best.pt", False
    latest = max(models, key=lambda path: path.stat().st_mtime)
    results = latest.parent.parent / "results.csv"
    detail = f"最新 {latest.parent.parent.name}"
    if results.is_file():
        detail += "，有 results.csv"
    detail += f"，类别应为 {expected_name}"
    return detail, True


def run_daily_check(project_root: Path) -> int:
    root = Path(project_root).expanduser().resolve()
    errors = 0
    print("Wenshi 每日只读检查（不连接 AGV/JAKA/D435，不修改数据）")
    try:
        import torch
        import ultralytics
        _status("训练依赖", "通过", f"ultralytics {ultralytics.__version__}，torch {torch.__version__}")
        _status("CUDA", "信息", "可用" if torch.cuda.is_available() else "不可用，当前使用 CPU")
    except ImportError as exc:
        _status("训练依赖", "失败", str(exc))
        errors += 1
    for model_type in ("plant", "panicle"):
        session_root = root / "yubei" / "data" / model_type
        sessions = [path for path in session_root.glob("dataset_*") if path.is_dir() and not path.name.startswith("dataset_normalized_")]
        normalized = [path for path in session_root.glob("dataset_normalized_*") if path.is_dir()]
        if sessions:
            for session in sorted(sessions):
                detail, ok = _dataset_summary(session, model_type)
                _status(
                    f"{model_type} 原始/标注数据/{session.name}",
                    "通过" if ok else "失败",
                    detail,
                )
                errors += not ok
        else:
            _status(f"{model_type} 原始/标注数据", "待办", "没有 dataset_* 会话")
        if normalized:
            for session in sorted(normalized):
                detail, ok = _dataset_summary(session, model_type)
                _status(
                    f"{model_type} 规范化数据/{session.name}",
                    "通过" if ok else "失败",
                    detail,
                )
                errors += not ok
        else:
            _status(f"{model_type} 规范化数据", "待办", "尚未生成规范化副本")
        detail, ok = _prepared_summary(root / "yubei" / "datasets" / model_type, model_type)
        _status(f"{model_type} 训练副本", "通过" if ok else "待办", detail)
        detail, ok = _model_summary(root / "yubei" / "training", model_type)
        _status(f"{model_type} 训练产物", "通过" if ok else "待办", detail)
    return int(errors)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Wenshi 每日离线只读检查")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    return run_daily_check(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
