"""Dedicated entry boundary for the 32-plant phenotype task.

This module intentionally does not reuse the legacy rice target state machine.
It validates the phenotype configuration before any hardware adapter is
constructed. Hardware execution is intentionally unavailable until the field
adapter, 16-stop calibration, Tag backend, and three arm postures are accepted.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .phenotyping.preflight import phenotyping_preflight
from .phenotyping.schedule import build_observation_schedule


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Wenshi 32 株表型巡检控制台，16 个停车点")
    parser.add_argument("--config", type=Path, default=Path("config/wenshi.yaml"))
    parser.add_argument("--runtime-root", type=Path, default=Path("runtime/runs"))
    parser.add_argument("--simulate", action="store_true", help="只验证 16 个停车点调度，不连接硬件")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    report = phenotyping_preflight(config, args.runtime_root)
    if not report.ok:
        for error in report.errors:
            print(f"表型预检失败: {error}")
        return 1
    if args.simulate:
        schedule = build_observation_schedule(config)
        print(f"表型模拟调度: {len(schedule)} 个停车位置，{sum(len(stop.plant_ids) for stop in schedule)} 株")
        return 0
    print("表型硬件执行适配器尚未现场验收；拒绝启动 AGV/JAKA。请先使用 --simulate 和现场测试清单完成验收。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
