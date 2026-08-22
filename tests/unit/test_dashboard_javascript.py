import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_javascript_formats_operator_values_without_fake_zeroes():
    if not shutil.which("gjs"):
        pytest.skip("gjs is not installed")

    script = f"""
globalThis.document = {{
  getElementById() {{
    return {{
      addEventListener() {{}},
      className: "",
      textContent: "",
      innerHTML: "",
      hidden: false,
      disabled: false,
    }};
  }},
  querySelectorAll() {{ return []; }},
}};
globalThis.window = {{ setInterval() {{}} }};
globalThis.fetch = async () => ({{ ok: true, json: async () => ({{ runs: [] }}) }});
imports.searchPath.unshift("dashboard/static");
const app = imports.app;

if (app.formatDistance(null) !== "未记录") throw new Error("null distance must stay unknown");
if (app.datasetNumber(null) !== "") throw new Error("null bbox coordinate must stay empty");
if (app.sideLabel("left") !== "左侧") throw new Error("left side label is incorrect");
if (app.statusInfo("finished").label !== "已完成") throw new Error("run status label is incorrect");
if (app.formatQuality({{ score: 0.92, ok: true }}) !== "92 分 · 合格") throw new Error("quality label is incorrect");
const targets = [
  {{ target_id: "T0001", status: "near_captured" }},
  {{ target_id: "T0002", status: "near_failed", failure_reason: "模糊" }},
  {{ target_id: "T0003", status: "far_captured" }},
];
if (app.filterTargets(targets, "complete").map((item) => item.target_id).join(",") !== "T0001") throw new Error("completed filter is incorrect");
if (app.filterTargets(targets, "issues").map((item) => item.target_id).join(",") !== "T0002") throw new Error("issues filter is incorrect");
if (app.adjacentTargetId(targets, "T0002", 1) !== "T0003") throw new Error("next target navigation is incorrect");
if (app.adjacentTargetId(targets, "T0001", -1) !== null) throw new Error("previous target boundary is incorrect");
print("dashboard presentation checks passed");
"""
    result = subprocess.run(
        ["gjs", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
