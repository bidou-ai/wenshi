"""Administrator-only soft deletion and dedupe reset."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import shutil


class AdminActions:
    def __init__(self, runtime_root: Path, pin: str):
        self.root = Path(runtime_root).expanduser().resolve()
        self.pin = str(pin)
        self.tokens: dict[str, float] = {}

    def authenticate(self, pin: str) -> str | None:
        if not self.pin:
            return None
        if secrets.compare_digest(str(pin), self.pin):
            token = secrets.token_urlsafe(24)
            self.tokens[token] = datetime.now().timestamp() + 1800
            return token
        return None

    def _check(self, token: str) -> None:
        if not self.pin:
            raise PermissionError("管理员功能未配置 PIN，当前已禁用")
        if token not in self.tokens or self.tokens[token] < datetime.now().timestamp():
            raise PermissionError("管理员 PIN 会话已失效")

    def _run(self, run_id: str, allow_running: bool = False) -> Path:
        if not run_id.startswith("run_") or "/" in run_id or ".." in run_id:
            raise ValueError("invalid run id")
        path = self.root / run_id
        if not path.is_dir():
            raise FileNotFoundError(run_id)
        value = json.loads((path / "run.json").read_text(encoding="utf-8")) if (path / "run.json").exists() else {}
        if value.get("status") == "running" and not allow_running:
            raise ValueError("不能删除正在运行的巡检")
        return path

    def soft_delete(self, run_id: str, target_id: str | None, token: str) -> dict:
        self._check(token)
        run = self._run(run_id)
        source = run / "targets" / target_id if target_id else run
        if target_id and (not target_id.startswith("T") or "/" in target_id or ".." in target_id):
            raise ValueError("invalid target id")
        if not source.exists():
            raise FileNotFoundError(source)
        trash = run / "trash" / target_id if target_id else self.root / ".trash" / run_id
        if trash.exists():
            raise ValueError(f"回收区已存在同名项目: {trash.name}")
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(trash))
        event_root = run if target_id else trash
        with (event_root / "admin_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "soft_delete", "target_id": target_id, "time": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
        return {"ok": True, "trash": str(trash)}

    def reset_dedupe(self, run_id: str, token: str) -> dict:
        self._check(token)
        run = self._run(run_id, allow_running=True)
        marker = run / "dedupe_reset.json"
        marker.write_text(
            json.dumps(
                {
                    "request_id": secrets.token_urlsafe(12),
                    "time": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {"ok": True, "marker": str(marker)}
