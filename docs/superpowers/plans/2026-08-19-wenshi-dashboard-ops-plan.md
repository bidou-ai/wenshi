# Wenshi Dashboard and Operations Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide a read-only local/LAN dashboard for patrol runs, administrator-only soft delete/reset/cleanup, and a complete Chinese operating manual covering software and control frameworks, startup, logs, datasets, training, recovery, and safe cleanup.

**Architecture:** A standard-library `ThreadingHTTPServer` serves JSON APIs, static HTML/CSS/JavaScript, and media files from the configured runtime root. The dashboard never imports ROS2 or hardware clients and cannot issue movement commands. Admin mutations require a configured PIN and use an audit event; filesystem cleanup is a separate explicit CLI command.

**Tech Stack:** Python 3.10 `http.server`, `json`, `pathlib`, HTML/CSS/vanilla JavaScript, pytest fixtures.

**Spec:** `docs/superpowers/specs/2026-08-19-wenshi-yubei-demo-design-zh.md`

## Global Constraints

- Default server is local-only; LAN binding requires an explicit `--host 0.0.0.0` or configured workstation address.
- Dashboard has no user accounts. Viewers browse; one administrator PIN unlocks mutations.
- No dashboard endpoint controls AGV, JAKA, camera, route, or model execution.
- Media paths must remain below the configured `runtime/runs` root.
- Target pages display one far and one near image and dynamically overlay bbox metadata.
- Soft delete hides a target/run and moves it to a recoverable trash directory; permanent cleanup is CLI-only.
- A running run directory cannot be deleted or cleaned.
- `liuyi666.md` records unresolved map, calibration, overlap, flower, GPU, and cross-run identity questions.

### Task 1: Add a Run Index and Safe Dashboard HTTP API

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/run_index.py`
- Create: `dashboard/server.py`
- Create: `dashboard/admin.py`
- Create: `tests/unit/test_dashboard_index.py`
- Create: `tests/unit/test_dashboard_api.py`

**Interfaces:**
- `RunIndex(runtime_root: Path).list_runs() -> list[dict]`
- `RunIndex.load_run(run_id: str) -> dict`
- `RunIndex.load_target(run_id: str, target_id: str) -> dict`
- `MediaResolver.resolve(run_id: str, target_id: str, filename: str) -> Path`
- `AdminActions.soft_delete(run_id: str, target_id: str | None, pin: str) -> dict`
- `AdminActions.reset_dedupe(run_id: str, pin: str) -> dict`
- API `GET /api/runs`, `GET /api/runs/<run_id>`, `GET /api/runs/<run_id>/targets/<target_id>`, `GET /media/...`, `POST /api/admin/auth`, `POST /api/admin/delete`, `POST /api/admin/reset-dedupe`

- [ ] **Step 1: Write fixture-based tests**

Create temporary run directories with `run.json`, `events.jsonl`, target metadata, and images. Test newest-first run listing, missing metadata handling, path traversal rejection, current-run deletion refusal, wrong PIN rejection, and successful soft delete.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_dashboard_index.py tests/unit/test_dashboard_api.py`
Expected: FAIL because dashboard modules do not exist.

- [ ] **Step 3: Implement index and safe resolvers**

Read only JSON metadata and event summaries, resolve path components with `Path.resolve()` and `relative_to(runtime_root)`, and use `ThreadingHTTPServer`. Do not use shell commands from handlers. Keep a run marked `running` undeletable.

- [ ] **Step 4: Implement PIN-scoped administrator actions**

Read the PIN from a config value or `WENSHI_ADMIN_PIN`, store no plaintext session longer than the process lifetime, add a short-lived random token for browser mutation requests, and append `admin_action` events. Soft delete moves the requested target/run under `<run>/trash/` and updates `deleted.json`.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=. pytest -q tests/unit/test_dashboard_index.py tests/unit/test_dashboard_api.py`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/__init__.py dashboard/run_index.py dashboard/server.py dashboard/admin.py tests/unit/test_dashboard_index.py tests/unit/test_dashboard_api.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add safe patrol dashboard API"
```

### Task 2: Build the Compact Results UI

**Files:**
- Create: `dashboard/static/index.html`
- Create: `dashboard/static/app.js`
- Create: `dashboard/static/style.css`
- Create: `tests/unit/test_dashboard_assets.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- `GET /` serves the single-page dashboard.
- JavaScript functions `loadRuns()`, `loadRun(runId)`, `renderTarget(target)`, `openAdminPanel()` consume only the JSON APIs.

- [ ] **Step 1: Write asset/API contract tests**

Assert the HTML references existing CSS and JS, the UI labels contain current/history runs, far/near, quality, failure, route segment, side, and target count, and no asset contains a movement command.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_dashboard_assets.py`
Expected: FAIL because static assets do not exist.

- [ ] **Step 3: Implement the UI**

Use a restrained operational layout: run list, status counters, target table, and a target detail pane with two native 1280x720 images. Draw bbox overlays on a canvas above the image from metadata, support image zoom without altering stored originals, show quality and failure reasons, and poll the current run at a bounded interval. Keep buttons icon-plus-text only for clear actions and avoid any motion control affordance.

- [ ] **Step 4: Run asset tests and serve a fixture**

Run: `PYTHONPATH=. pytest -q tests/unit/test_dashboard_assets.py`; start `python3 dashboard/server.py --root <fixture-root> --host 127.0.0.1 --port 8088` and verify `/`, `/api/runs`, and one `/media/...` response.

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/index.html dashboard/static/app.js dashboard/static/style.css tests/unit/test_dashboard_assets.py docs/ARCHITECTURE.md
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add patrol results dashboard UI"
```

### Task 3: Add Permanent Cleanup CLI and Admin Documentation

**Files:**
- Create: `dashboard/cleanup.py`
- Create: `tests/unit/test_dashboard_cleanup.py`
- Modify: `config/wenshi.yaml`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/SAFETY.md`

**Interfaces:**
- `preview_cleanup(runtime_root: Path, run_ids: list[str]) -> CleanupPlan`
- `execute_cleanup(plan: CleanupPlan, confirm: str) -> CleanupResult`
- CLI commands `python3 dashboard/cleanup.py --root ... --list`, `--preview run_...`, and `--execute run_... --confirm <run_id>`

- [ ] **Step 1: Write destructive-action tests**

Test that preview is non-mutating, active runs are refused, paths outside `runtime/runs` are refused, an exact run ID confirmation is required, and deleted runs are reported with file counts and bytes.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_dashboard_cleanup.py`
Expected: FAIL because cleanup planner and executor do not exist.

- [ ] **Step 3: Implement recoverable cleanup**

The dashboard handles soft delete only. The CLI prints exact paths and sizes, requires `--confirm` equal to the run ID, refuses the current run and symlink escapes, and then removes only the selected historical trash/run directory. Never accept a broad root or wildcard deletion argument.

- [ ] **Step 4: Update operations and safety docs**

Document runtime layout, `events.jsonl`, `system.log`/`demo.log`, `camera.log`, `agv.csv`, `jaka.csv`, target metadata, yubei dataset logs, where to inspect failed runs, how to reset dedupe, and how to preview/execute cleanup. Explain route motion versus target-alignment reverse and the rear-radar warning.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=. pytest -q tests/unit/test_dashboard_cleanup.py tests/unit/test_delivery_files.py`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/cleanup.py tests/unit/test_dashboard_cleanup.py config/wenshi.yaml docs/OPERATIONS.md docs/SAFETY.md
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add explicit patrol cleanup workflow"
```

### Task 4: Add the Pending-Questions File and Complete Operator Manual

**Files:**
- Create: `liuyi666.md`
- Create: `docs/USER_MANUAL.md`
- Modify: `README.md`
- Modify: `docs/PROJECT_STATUS.md`
- Modify: `docs/VISION_STATUS.md`
- Create: `tests/unit/test_manual_and_pending_docs.py`

**Interfaces:**
- `liuyi666.md` must contain the three-region map question, future hand-eye/+-10cm requirement, overlap examples, future flower close-up, GPU/VM decision, and cross-run plant identity.
- `docs/USER_MANUAL.md` must explain architecture, state flow, startup/shutdown, yubei commands, dataset annotation/training/publish, dashboard, logs, recovery, and cleanup.

- [ ] **Step 1: Write documentation-content tests**

Assert the manual references `wenshi/yubei`, `runtime/runs`, `events.jsonl`, `system.log`/`demo.log`, `camera.log`, `agv.csv`, `jaka.csv`, `LM1 -> LM4 -> LM3 -> LM2`, target alignment reverse, J5 follow, administrator PIN, and cleanup preview. Assert `liuyi666.md` contains all five pending topics.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_manual_and_pending_docs.py`
Expected: FAIL because the files and required sections do not exist.

- [ ] **Step 3: Write the operator manual**

Use Chinese headings and exact commands. Separate dataset capture from formal patrol capture, explain one run/one target folder, describe target loss/camera loss/AGV block/JAKA failure recovery, state that ordinary route reverse remains disabled, and give safe log cleanup commands.

- [ ] **Step 4: Run documentation tests and link checks**

Run: `PYTHONPATH=. pytest -q tests/unit/test_manual_and_pending_docs.py tests/unit/test_project_structure.py`; run `rg -n "TODO|TBD|placeholder|implement later" liuyi666.md docs/USER_MANUAL.md dashboard` and expect no output.

- [ ] **Step 5: Commit**

```bash
git add liuyi666.md docs/USER_MANUAL.md README.md docs/PROJECT_STATUS.md docs/VISION_STATUS.md tests/unit/test_manual_and_pending_docs.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "docs: add Wenshi operator manual and pending questions"
```

## Plan Completion Check

Run `PYTHONPATH=app:. pytest -q`, `python3 -m compileall app yubei dashboard`, and a fixture dashboard smoke test. Confirm no dashboard or yubei file imports ROS2, no dashboard route contains an AGV/JAKA command, and cleanup refuses the active run.
