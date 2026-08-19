"""Run-scoped logging for the formal rice patrol package."""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(self, logs_root: str | Path):
        configured = os.environ.get("WENSHI_RUN_DIR", "").strip()
        if configured:
            self.run_dir = Path(configured).expanduser().resolve()
        else:
            stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
            self.run_dir = Path(logs_root).expanduser().resolve() / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logs_root = self.run_dir.parent
        self._lock = threading.Lock()

        self.logger = logging.getLogger(f"wenshi_patrol.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = logging.FileHandler(self.run_dir / "system.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        self.events_path = self.run_dir / "events.jsonl"
        self.agv_path = self.run_dir / "agv.csv"
        self.jaka_path = self.run_dir / "jaka.csv"
        self._write_csv_header(
            self.agv_path,
            ["time", "state", "x", "y", "angle", "vx_cmd", "w_cmd", "blocked", "emergency"],
        )
        self._write_csv_header(
            self.jaka_path,
            ["time", "state", "j1", "j2", "j3", "j4", "j5", "j6"],
        )

    @staticmethod
    def _write_csv_header(path: Path, fields: list[str]):
        if path.exists() and path.stat().st_size:
            return
        with path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerow(fields)

    def info(self, message: str):
        self.logger.info(message)

    def error(self, message: str):
        self.logger.error(message)

    def event(self, event: str, **values: Any):
        record = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            **values,
        }
        with self._lock, self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.info(f"{event}: {values}")

    def sample_agv(self, state: str, status: dict[str, Any], vx_cmd: float, w_cmd: float):
        row = [
            time.time(),
            state,
            status.get("x"),
            status.get("y"),
            status.get("angle"),
            vx_cmd,
            w_cmd,
            status.get("blocked"),
            status.get("emergency"),
        ]
        with self._lock, self.agv_path.open("a", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerow(row)

    def sample_jaka(self, state: str, joint: list[float] | None):
        values = list(joint[:6]) if joint else [None] * 6
        row = [time.time(), state, *values]
        with self._lock, self.jaka_path.open("a", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerow(row)

    def previous_runs(self) -> list[Path]:
        if not self.logs_root.exists():
            return []
        return sorted(
            path
            for path in self.logs_root.iterdir()
            if path.is_dir() and path.name.startswith("run_") and path.resolve() != self.run_dir
        )
