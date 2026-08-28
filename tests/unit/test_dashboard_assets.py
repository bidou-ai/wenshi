import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class _DashboardMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def element(self, element_id):
        return next((item for item in self.elements if item[1].get("id") == element_id), None)


def _markup():
    parser = _DashboardMarkup()
    parser.feed((ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8"))
    return parser


def _css_px_variable(name):
    css = (ROOT / "dashboard" / "static" / "style.css").read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(name)}\s*:\s*([0-9.]+)px", css)
    assert match, f"missing CSS size variable {name}"
    return float(match.group(1))


def _css_rule(selector):
    css = (ROOT / "dashboard" / "static" / "style.css").read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", css)
    assert match, f"missing CSS rule {selector}"
    return match.group(1)


def test_dashboard_exposes_an_accessible_plant_inspection_workbench():
    markup = _markup()

    navigator = markup.element("plantNavigator")
    workspace = markup.element("inspectionWorkspace")
    comparison = markup.element("viewComparison")

    assert navigator and navigator[0] == "aside"
    assert workspace and workspace[0] == "section"
    assert comparison and comparison[1]["aria-label"] == "左中右三视角 RGB-D 证据"
    assert markup.element("runSelector")[0] == "select"
    assert markup.element("plantFilters")[1]["aria-label"] == "植株筛选"
    assert markup.element("plants")[1]["aria-live"] == "polite"
    assert markup.element("previousPlant")[0] == "button"
    assert markup.element("nextPlant")[0] == "button"


def test_dashboard_has_operator_status_and_refresh_feedback():
    markup = _markup()

    assert markup.element("systemStatus")[1]["role"] == "status"
    assert markup.element("selectedRun")
    assert markup.element("lastRefresh")
    assert markup.element("errorBanner")[1]["role"] == "alert"


def test_dashboard_reading_sizes_meet_the_large_operator_display_contract():
    assert _css_px_variable("--font-body") >= 20
    assert _css_px_variable("--font-control") >= 18
    assert _css_px_variable("--font-label") >= 16
    assert _css_px_variable("--font-title") >= 32
    assert _css_px_variable("--font-metric") >= 36


def test_dashboard_primary_surfaces_are_soft_and_visually_layered():
    assert 8 <= _css_px_variable("--radius-surface") <= 10
    assert 6 <= _css_px_variable("--radius-control") <= 8

    photo = _css_rule(".photo-pane")
    record = _css_rule(".plant-record")
    evidence = _css_rule(".evidence-section")

    assert "border-radius: var(--radius-surface)" in photo
    assert "box-shadow: var(--shadow-surface)" in photo
    assert "border-radius: var(--radius-item)" in record
    assert "border-radius: var(--radius-surface)" in evidence


def test_dashboard_avoids_rebuilding_every_region_with_hard_grid_lines():
    css = (ROOT / "dashboard" / "static" / "style.css").read_text(encoding="utf-8")
    hard_dividers = re.findall(r"border-(?:top|right|bottom|left):\s*1px solid", css)
    assert len(hard_dividers) <= 20


def test_dashboard_assets_are_bound_to_phenotyping_evidence_not_legacy_patrol_media():
    html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")

    assert "三视角" in html and "RGB-D" in html and "株高" in script and "有效穗" in script
    assert "/api/phenotype/runs" in script
    assert '"/api/runs"' not in script
    assert '"/media/' not in script
    assert (ROOT / "dashboard" / "static" / "app.js").is_file()
    assert (ROOT / "dashboard" / "static" / "style.css").is_file()
