"""Validate and explicitly publish staged JAKA viewpoint files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import shutil

try:
    from .teach_viewpoints import VIEWPOINT_NAMES
except ImportError:  # direct module execution
    from teach_viewpoints import VIEWPOINT_NAMES


@dataclass
class VerificationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)


def verify_viewpoints(path: Path, max_joint_step_deg: float = 120.0) -> VerificationReport:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return VerificationReport(False, [f"无法读取示教文件: {exc}"])
    errors = []
    joints: dict[str, list[float]] = {}
    for name in VIEWPOINT_NAMES:
        entry = value.get(name) if isinstance(value, dict) else None
        joint = entry.get("joint") if isinstance(entry, dict) else None
        if not isinstance(joint, list) or len(joint) != 6:
            errors.append(f"缺少或无效示教点: {name}")
            continue
        try:
            converted = [float(item) for item in joint]
        except (TypeError, ValueError):
            errors.append(f"示教点关节值无效: {name}")
            continue
        if not all(math.isfinite(item) for item in converted):
            errors.append(f"示教点关节值无效: {name}")
            continue
        joints[name] = converted
    for left, right in zip(VIEWPOINT_NAMES, VIEWPOINT_NAMES[1:]):
        first = joints.get(left)
        second = joints.get(right)
        if first is not None and second is not None:
            delta = max(abs(second[i] - first[i]) for i in range(6))
            if delta > float(max_joint_step_deg):
                errors.append(f"{left}->{right} 关节变化 {delta:.1f}deg 超过 {float(max_joint_step_deg):.1f}deg")
    return VerificationReport(not errors, errors)


def publish_viewpoints(staged: Path, formal: Path, backup_dir: Path) -> Path:
    report = verify_viewpoints(staged)
    if not report.ok:
        raise ValueError("示教文件校验失败: " + "；".join(report.errors))
    staged = Path(staged).expanduser().resolve()
    formal = Path(formal).expanduser().resolve()
    backup_dir = Path(backup_dir).expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    if formal.exists():
        backup = backup_dir / f"{formal.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(formal, backup)
    formal.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staged, formal)
    return backup if formal.exists() and 'backup' in locals() else formal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="校验并发布 JAKA 八点示教文件")
    parser.add_argument("path", type=Path)
    parser.add_argument("--publish-to", type=Path)
    parser.add_argument("--backup-dir", type=Path, default=Path("yubei/backups"))
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    report = verify_viewpoints(args.path)
    print(json.dumps({"ok": report.ok, "errors": report.errors}, ensure_ascii=False, indent=2))
    if args.publish_to:
        if not args.confirm:
            raise SystemExit("发布需要 --confirm")
        print(publish_viewpoints(args.path, args.publish_to, args.backup_dir))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
