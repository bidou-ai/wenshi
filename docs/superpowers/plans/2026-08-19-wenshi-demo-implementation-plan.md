# Wenshi Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The three component plans below are independently testable and should be executed in the listed order.

**Goal:** Build the removable `yubei` preparation toolbox, the first Wenshi rice patrol task layer, and a local run-results dashboard without giving `yubei` a runtime dependency on formal Wenshi control.

**Architecture:** Keep AGV/JAKA/D435 clients as the only hardware boundaries. Add pure target-selection, quality, dedupe, and run-storage modules before integrating a guarded task state machine into `WenshiPatrolNode`. Run the dashboard and annotation server with Python standard-library HTTP servers so the Ubuntu runtime does not require a web framework.

**Tech Stack:** Python 3.10, ROS2 Humble/rclpy, OpenCV, NumPy, PyYAML, Ultralytics YOLO as an optional training/inference dependency, HTML/CSS/vanilla JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-08-19-wenshi-yubei-demo-design-zh.md`

## Global Constraints

- Formal route is `LM1 -> LM4 -> LM3 -> LM2 -> LM1` when loop mode is enabled.
- `yubei` is standalone and must not import `wenshi_patrol` or control formal runtime devices.
- Route patrol only sends positive forward velocity; only an explicit target-alignment state may send low-speed reverse velocity.
- Reverse target alignment is hard limited to `0.05m/s` initial speed and `0.60m` per target.
- JAKA commands have one owner at a time: patrol sweep, target-follow, or fixed approach/retract.
- One patrol run writes one `run_<timestamp>` directory; one target keeps one `far.jpg` and one `near.jpg`.
- Rice is class `0`, flower is class `1`; first training run uses rice only.
- No hand-eye calibration, map-coordinate `+-10cm`, flower inference, multi-user accounts, or public internet exposure in this phase.
- Every behavior change gets a focused pytest test before the implementation is considered complete.

## Plan Files and Order

1. `docs/superpowers/plans/2026-08-19-wenshi-yubei-plan.md` — removable preparation tools and training workflow.
2. `docs/superpowers/plans/2026-08-19-wenshi-runtime-plan.md` — formal vision, storage, guarded target task, and route integration.
3. `docs/superpowers/plans/2026-08-19-wenshi-dashboard-ops-plan.md` — dashboard, admin cleanup, `liuyi666.md`, and the complete operator manual.

Run the first plan without a ROS2 runtime. Run the second plan with all device clients mocked until the final supervised hardware checkpoint. Run the third plan against fixture run directories before exposing the server on the greenhouse LAN.

## Cross-Plan Verification

After each plan, run:

```bash
PYTHONPATH=app pytest -q
python3 -m compileall app yubei dashboard
```

The complete final verification additionally runs the yubei CLI help commands, a temporary dashboard server against fixture data, and the existing formal preflight script without enabling reverse motion.
