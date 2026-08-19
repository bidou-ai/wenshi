"""将采集和识别产物限制在当前运行目录。"""

from __future__ import annotations

from pathlib import Path


class VisionRunStore:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir).expanduser().resolve()
        if not self.run_dir.name.startswith("run_"):
            raise ValueError("视觉产物只能写入 runtime/run_* 目录")
        self.data_dir = self.run_dir / "vision"
        self.image_dir = self.data_dir / "images"
        self.record_path = self.data_dir / "detections.jsonl"

    def ensure_directories(self):
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def image_paths(self, timestamp: str, action: str) -> tuple[Path, Path, Path]:
        self.ensure_directories()
        prefix = f"{timestamp}_{action}"
        return (
            self.image_dir / f"{prefix}_color.jpg",
            self.image_dir / f"{prefix}_depth.png",
            self.image_dir / f"{prefix}_annotated.jpg",
        )

