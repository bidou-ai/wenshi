import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_javascript_formats_phenotype_values_and_filters_review_work():
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

if (app.formatMeters(null) !== "未复核") throw new Error("null height must stay unreviewed");
if (app.formatMeters(0.82) !== "82.0 cm") throw new Error("height unit is incorrect");
if (app.statusInfo("finished").label !== "已完成") throw new Error("run status label is incorrect");
const plants = [
  {{ plant_id: "A-01", status: "complete", missing_views: [], review: {{ state: "reviewed" }} }},
  {{ plant_id: "A-02", status: "complete", missing_views: [], review: {{ state: "pending" }} }},
  {{ plant_id: "A-03", status: "partial", missing_views: ["right"], review: {{ state: "pending" }} }},
];
if (app.filterPlants(plants, "complete").map((item) => item.plant_id).join(",") !== "A-01,A-02") throw new Error("complete filter is incorrect");
if (app.filterPlants(plants, "review").map((item) => item.plant_id).join(",") !== "A-02,A-03") throw new Error("review filter is incorrect");
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
