# Wenshi Field Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one local field-test entry for camera preview, read-only teaching, AGV one-loop map motion, and taught JAKA point motion.

**Architecture:** Keep `yubei` independent from the formal patrol runtime, but reuse the tested AGV/JAKA clients and route math in one `yubei/field_test.py` motion process. The shell launcher is the only field-test entry; it may start the read-only D435 ROS2 bridge and RViz beside the motion process, and never starts the patrol controller.

**Tech Stack:** Python 3, OpenCV, NumPy, PyYAML, existing Wenshi AGV/JAKA clients, existing Wens1 map parser and route math.

**Spec:** `docs/superpowers/specs/2026-08-25-field-test-design-zh.md`

## Global Constraints

- Never call JAKA power, enable, disable, or power-off commands.
- Ordinary map motion never sends negative `vx`; emergency stop and watchdog remain active.
- The field-test process is the only AGV/JAKA motion client during a field-test session; ROS visualization reads status and never commands motion.
- Teaching output is staged under `runtime/field_tests/` and never overwrites formal viewpoints.
- Dataset, labeling, training, dashboard, and formal patrol code remain available but are not started by this entry.
- Ordinary blocked state pauses the current route segment and resumes after `field_test.blocked_clear_s` of stable clearance; emergency stop remains terminal.
- Route attachment snaps to an LM station within `field_test.station_snap_m` before comparing adjoining segments.
- Field-test route speed is controlled by `field_test.route_speed_mps` and defaults to `0.10m/s`, separate from formal patrol speed.

### Task 1: Lock the read-only JAKA teaching protocol

**Files:**
- Modify: `yubei/teach_protocol.py`
- Modify: `yubei/teach_viewpoints.py`
- Test: `tests/unit/test_yubei_viewpoints.py`

**Interfaces:**
- `TeachingClient.read_joint()` accepts `joint_pos`, `jointPosition`, `joint`, and `data` list responses.
- `TeachingClient.read_tcp()` accepts `tcp_pos`, `tcpPosition`, `tcp`, and `data` list responses.
- `TeachingClient.read_snapshot()` connects, queries both values, closes the socket, and returns `(joint, tcp)`.

- [x] Write failing tests for real `joint_pos`/`tcp_pos` fields and one-shot reconnect behavior.
- [x] Run the focused tests and confirm the new tests fail before implementation.
- [x] Implement response aliases, contextual timeout errors, and `read_snapshot()`.
- [x] Run the focused tests and the full unit suite.

### Task 2: Add the single field-test session

**Files:**
- Create: `yubei/field_test.py`
- Create: `tests/unit/test_field_test.py`

**Interfaces:**
- `FieldTestSession(config_path: Path, output_root: Path, preview: bool = True)` owns one session and exposes `run_console(input_stream, output_stream)`.
- `RouteRunner.run_one_loop()` returns a result dict with `ok`, `attach_segment`, `route`, and `error`.
- `ArmPointTester.run(points_path, input_stream, output_stream)` returns a result dict with `ok`, `completed`, and `error`.

- [x] Write failing tests for nearest-route attachment, closed-loop segment order, non-blocking stop handling, and point-order validation.
- [x] Run the focused test file and confirm the expected failures.
- [x] Implement camera preview worker, session logging, status/stop handling, route runner, and arm point tester using existing clients.
- [x] Add LM station snap, blocked pause/resume, and a dedicated field-test route speed.
- [x] Run focused tests, then test `python3 yubei/field_test.py --help` without hardware.

### Task 3: Make the launcher the only field-test entry

**Files:**
- Create: `scripts/start_field_test.sh`
- Modify: `yubei/README.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/FIELD_TEST_CHECKLIST.md`
- Modify: `docs/USER_MANUAL.md`
- Test: `tests/unit/test_manual_and_pending_docs.py`

- [x] Update the field-test operating procedure and launcher documentation.
- [x] Implement the launcher with config/output arguments.
- [x] Launch the D435 ROS2 bridge and RViz from the same entry, with `--no-rviz` fallback.
- [x] Run documentation tests and shell syntax checks.

### Task 4: Verify and clean the field-test surface

**Files:**
- Modify: `yubei/start_yubei.sh` only if its help text needs to point to the new entry.
- Modify: `yubei/README.md` if command ownership is ambiguous.

- [x] Run all unit tests and `bash -n scripts/start_field_test.sh`.
- [x] Run static searches for accidental power/enable commands in field-test code.
- [x] Verify that no field-test path writes `config/viewpoints.json` or `runtime/runs/`.
- [x] Report hardware-dependent checks that were not run on the offline workstation.
