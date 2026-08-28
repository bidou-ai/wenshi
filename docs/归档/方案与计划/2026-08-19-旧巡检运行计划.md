# Wenshi Formal Patrol Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add rice target selection, target following, controlled alignment reverse, fixed approach/retract, run-scoped storage, quality checks, and guarded integration to the existing Wenshi patrol node.

**Architecture:** Keep `WenshiPatrolNode` as the ROS2 owner of AGV/JAKA/camera subscriptions, but move all target math and persistence into small pure modules. A `TargetTask` object owns the transition from confirmation through alignment, fixed approach, near capture, and resume; the node remains the only caller of AGV/JAKA clients. Fake AGV, fake arm, fake detector, and recorded frame fixtures test all safety branches before hardware use.

**Tech Stack:** Python 3.10, ROS2 Humble/rclpy, OpenCV, NumPy, PyYAML, existing AGV/JAKA clients, optional Ultralytics detector.

**Spec:** `docs/superpowers/specs/2026-08-19-wenshi-yubei-demo-design-zh.md`

## Global Constraints

- `PATROL_FORWARD` follows `LM1 -> LM4 -> LM3 -> LM2 -> LM1` and never commands negative velocity.
- Only `ALIGN_REVERSE` may command negative velocity; it is capped at `0.05m/s` initial speed and `0.60m` per target.
- `rear_radar_verified` remains false and produces a warning; the guarded target state requires an operator-visible warning but does not enable arbitrary reverse commands.
- Stable target rule is at least 3 valid observations in the latest 5 frames.
- Select the valid rice bbox closest to the image center; lock side and do not switch side during one target task.
- A target within 30cm of the selected target is deferred for the current loop; selected targets are deduplicated for 2 hours.
- Each target stores only one far RGB JPG and one near RGB JPG; metadata contains boxes, quality, depth summary, route segment, side, and failure reason.
- Near capture holds the current near pose, captures up to 3 bursts of 5 frames in memory, and persists only the best frame.
- JAKA command ownership must be explicit and mutually exclusive.

### Task 1: Define Runtime Configuration and Run-Scoped Storage

**Files:**
- Create: `app/wenshi_patrol/vision/run_store.py`
- Create: `app/wenshi_patrol/vision/run_schema.py`
- Modify: `app/wenshi_patrol/logging_utils.py`
- Modify: `app/wenshi_patrol/vision/storage.py`
- Modify: `config/wenshi.yaml`
- Create: `tests/unit/test_run_store.py`
- Modify: `tests/unit/test_vision_storage.py`

**Interfaces:**
- `PatrolRunStore.create(root: Path) -> PatrolRunStore`
- `PatrolRunStore.create_target() -> TargetStore`
- `TargetStore.save_far(image: np.ndarray, metadata: dict) -> Path`
- `TargetStore.save_near(image: np.ndarray, metadata: dict) -> Path`
- `TargetStore.write_metadata(metadata: dict) -> None`
- `PatrolRunStore.append_event(event: str, **values) -> None`
- `PatrolRunStore.soft_delete(target_id: str) -> Path`

- [ ] **Step 1: Write failing tests for the new directory contract**

```python
def test_target_store_keeps_only_far_near_and_metadata(tmp_path):
    store = PatrolRunStore.create(tmp_path / "runs")
    target = store.create_target()
    target.save_far(np.zeros((4, 5, 3), dtype=np.uint8), {"side": "left"})
    target.save_near(np.ones((4, 5, 3), dtype=np.uint8), {"quality": 0.8})
    assert sorted(path.name for path in target.path.iterdir()) == ["far.jpg", "metadata.json", "near.jpg"]

def test_store_rejects_writes_outside_run_root(tmp_path):
    with pytest.raises(ValueError, match="run_"):
        PatrolRunStore(tmp_path / "ad_hoc")
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `PYTHONPATH=app pytest -q tests/unit/test_run_store.py tests/unit/test_vision_storage.py`
Expected: FAIL because the target store does not exist and old storage has the wrong contract.

- [ ] **Step 3: Implement atomic run and target storage**

Use `run_<timestamp>` directories, `targets/T0001/`, native RGB JPEG quality 95, metadata JSON, `events.jsonl`, and preserve existing `vision/` compatibility for the old `collect` command. Add config keys for `target_root`, `far_jpeg_quality`, `near_jpeg_quality`, and `operator_warning_only_reverse`.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=app pytest -q tests/unit/test_run_store.py tests/unit/test_vision_storage.py`
Expected: PASS, including all existing storage confinement tests.

- [ ] **Step 5: Commit**

```bash
git add app/wenshi_patrol/vision/run_store.py app/wenshi_patrol/vision/run_schema.py app/wenshi_patrol/logging_utils.py app/wenshi_patrol/vision/storage.py config/wenshi.yaml tests/unit/test_run_store.py tests/unit/test_vision_storage.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add patrol run and target storage"
```

### Task 2: Implement Stable Target Selection, Depth, Quality, and Dedupe

**Files:**
- Create: `app/wenshi_patrol/vision/targeting.py`
- Create: `app/wenshi_patrol/vision/quality.py`
- Create: `app/wenshi_patrol/vision/dedupe.py`
- Modify: `app/wenshi_patrol/vision/detector.py`
- Create: `tests/unit/test_targeting.py`
- Create: `tests/unit/test_quality.py`
- Create: `tests/unit/test_dedupe.py`

**Interfaces:**
- `TargetObservation(detection: Detection, image_width: int, image_height: int, depth_m: float | None, route_segment: str, along_track_m: float, timestamp: float)`
- `StableTargetTracker(window_size: int = 5, min_hits: int = 3).observe(...) -> TargetCandidate | None`
- `choose_center_target(detections: list[Detection], image_width: int, image_height: int) -> Detection | None`
- `side_from_bbox(detection: Detection, image_width: int) -> Literal["left", "right"]`
- `robust_bbox_depth(depth: np.ndarray, detection: Detection, sample_ratio: float = 0.35) -> DepthSummary`
- `score_frame(image: np.ndarray, detection: Detection, depth_summary: DepthSummary | None, expected_upper_body: bool) -> QualityResult`
- `DedupeRegistry(ttl_s: float = 7200, suppression_radius_m: float = 0.30)`

- [ ] **Step 1: Write failing pure-function tests**

Cover center-most selection over highest confidence, left/right split, 3-of-5 stability, invalid depth rejection, 30cm current-loop suppression, 2-hour TTL, and a new run resetting the registry. Add synthetic image tests for blur, exposure, clipping, crop completeness, bbox size, and depth variance.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=app pytest -q tests/unit/test_targeting.py tests/unit/test_quality.py tests/unit/test_dedupe.py`
Expected: FAIL because the modules and types do not exist.

- [ ] **Step 3: Implement target and depth math**

Represent detections in pixel coordinates, choose the target by Euclidean distance from image center among rice detections, compute side from bbox center relative to image center, sample only finite positive depth pixels within the inner bbox, and return median, MAD, valid ratio, and sample count. Do not invoke motion from these modules.

- [ ] **Step 4: Implement quality and dedupe rules**

Use variance of Laplacian, luminance percentile bounds, bbox margins, target area ratio, and depth MAD. Keep thresholds in config. Store selected target location in route coordinates only as a local segment/along-track key; do not pretend it is a map coordinate. Mark surrounding detections deferred for the current loop and selected target blocked for 7200 seconds.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=app pytest -q tests/unit/test_targeting.py tests/unit/test_quality.py tests/unit/test_dedupe.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/wenshi_patrol/vision/targeting.py app/wenshi_patrol/vision/quality.py app/wenshi_patrol/vision/dedupe.py app/wenshi_patrol/vision/detector.py tests/unit/test_targeting.py tests/unit/test_quality.py tests/unit/test_dedupe.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add rice target selection quality and dedupe"
```

### Task 3: Add Explicit Motion Ownership and Target-Follow Control

**Files:**
- Create: `app/wenshi_patrol/target_task.py`
- Create: `app/wenshi_patrol/target_follow.py`
- Modify: `app/wenshi_patrol/controller_math.py`
- Modify: `app/wenshi_patrol/arm_controller.py`
- Modify: `app/wenshi_patrol/agv.py`
- Create: `tests/unit/test_target_follow.py`
- Create: `tests/unit/test_target_task.py`
- Modify: `tests/unit/test_controller_math.py`

**Interfaces:**
- `TargetTaskState = Literal["CONFIRM", "FAR_CAPTURE", "ALIGN_REVERSE", "RELOCALIZE", "FIXED_APPROACH", "NEAR_CAPTURE", "RETRACT", "ABORT"]`
- `TargetTask.tick(observation: TaskObservation) -> TaskCommand`
- `TargetTask.stop(reason: str) -> None`
- `TargetFollowController.update(detection: Detection, image_width: int, dt_s: float) -> J5Command`
- `MotionOwner.acquire(owner: str) -> bool`, `release(owner: str) -> None`, `assert_owner(owner: str) -> None`
- `reverse_target_velocity(distance_remaining_m: float, configured_speed_mps: float, hard_limit_m: float) -> float`

- [ ] **Step 1: Write safety-first tests**

Assert route state rejects negative velocity, target alignment accepts only capped negative velocity, any stale camera or lost target returns zero command, side cannot change after lock, J5 follow error is clamped, and only the owner holding the token may send a joint command.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=app pytest -q tests/unit/test_target_follow.py tests/unit/test_target_task.py tests/unit/test_controller_math.py`
Expected: FAIL because the target task and ownership token do not exist.

- [ ] **Step 3: Implement pure target-follow and task transitions**

Use bbox horizontal error to generate a bounded J5 correction; preserve the locked side; stop on `camera_age > camera_timeout_s`, depth invalid, target missing, AGV blocked/emergency, or distance hard limit. The task emits commands and events; it does not directly open sockets.

- [ ] **Step 4: Add controlled arm ownership hooks**

Pause the normal J5 sweep before target follow, acquire the target-follow owner, release it before fixed approach, and always release it in `finally` on abort. Existing fixed approach remains disabled until config explicitly enables it.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=app pytest -q tests/unit/test_target_follow.py tests/unit/test_target_task.py tests/unit/test_controller_math.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/wenshi_patrol/target_task.py app/wenshi_patrol/target_follow.py app/wenshi_patrol/controller_math.py app/wenshi_patrol/arm_controller.py app/wenshi_patrol/agv.py tests/unit/test_target_follow.py tests/unit/test_target_task.py tests/unit/test_controller_math.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add guarded target alignment and j5 follow"
```

### Task 4: Extend Fixed Teaching for Home-Safe and Small Composition Correction

**Files:**
- Modify: `app/wenshi_patrol/fixed_approach.py`
- Modify: `app/wenshi_patrol/arm_controller.py`
- Modify: `config/viewpoints.json`
- Modify: `config/fixed_targets.json`
- Modify: `tests/unit/test_fixed_approach.py`
- Create: `tests/unit/test_composition_correction.py`

**Interfaces:**
- `SIDE_POSES` includes `camera_left/right`, `left_pre/photo`, and `right_pre/photo`.
- `HOME_SAFE_POSE = "home_safe"`
- `bounded_composition_delta(side: str, pixel_error: tuple[float, float], limits: dict) -> dict[int, float]`
- `validate_home_safe(viewpoints: dict) -> list[str]`
- `plan_fixed_sequence(viewpoints: dict, side: str, composition_delta: dict[int, float] | None) -> list[str]`

- [ ] **Step 1: Write tests**

Cover missing `home_safe`, invalid side, J4/J5/J6-only correction, rejection of J1/J2/J3 correction, correction limits, and required entry/pre/photo/retract order.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=app pytest -q tests/unit/test_fixed_approach.py tests/unit/test_composition_correction.py`
Expected: FAIL for new home-safe and correction interfaces.

- [ ] **Step 3: Implement validation and bounded correction**

Do not invent a free-space planner. Apply only configured small deltas to J4/J5/J6 around a taught pose, then require the existing return corridor check. The eight points are read from `viewpoints.json`; current historical values remain until yubei publishes new values.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=app pytest -q tests/unit/test_fixed_approach.py tests/unit/test_composition_correction.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/wenshi_patrol/fixed_approach.py app/wenshi_patrol/arm_controller.py config/viewpoints.json config/fixed_targets.json tests/unit/test_fixed_approach.py tests/unit/test_composition_correction.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: validate home safe and bounded composition correction"
```

### Task 5: Integrate Target Tasks with Camera Frames and Route Progress

**Files:**
- Modify: `app/wenshi_patrol/patrol_controller.py`
- Modify: `app/wenshi_patrol/vision/storage.py`
- Modify: `app/wenshi_patrol/vision/guard.py`
- Modify: `config/wenshi.yaml`
- Modify: `tests/unit/test_route_controller.py`
- Create: `tests/unit/test_patrol_target_integration.py`
- Create: `tests/integration/test_patrol_replay.py`

**Interfaces:**
- `WenshiPatrolNode._target_tick() -> None`
- `WenshiPatrolNode._route_detection_allowed(progress: SegmentProgress) -> bool`
- `WenshiPatrolNode._resume_patrol_after_target(reason: str) -> None`
- `WenshiPatrolNode._capture_target_frame(kind: Literal["far", "near"], ...) -> Path`

- [ ] **Step 1: Write integration tests with fake clients**

Test route loop includes the closing segment, station safety bands suppress detection, stable detections create one target, far capture happens before reverse, J5 follow occurs during reverse, fixed approach receives locked side, one near image is persisted, and patrol resumes in the same direction. Test target loss and camera failure stop both AGV and arm.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=app pytest -q tests/unit/test_patrol_target_integration.py tests/integration/test_patrol_replay.py`
Expected: FAIL because the node has no target-task integration.

- [ ] **Step 3: Add guarded configuration**

Add `vision.enabled`, `vision.motion_enable`, model path, target class `rice`, stability window, station safety band, target follow gains, reverse speed/hard distance, composition limits, quality thresholds, dedupe TTL/radius, and `fixed_approach.enabled`. Preserve the loader rule that motion is refused unless explicitly enabled and model path exists.

- [ ] **Step 4: Integrate the tick without changing hardware client ownership**

On each fresh color/depth pair, calculate route progress, skip station bands, call detector/tracker, capture far once, stop AGV and sweep, enter the target task, send reverse velocity only while `ALIGN_REVERSE`, then run fixed approach/near burst/retract synchronously under the arm owner. Every transition writes `events.jsonl`. Existing `start` remains one route; `start loop` uses the closing segment.

- [ ] **Step 5: Replace old detect storage behavior only for target tasks**

Keep `collect` and `detect` CLI commands compatible, but target tasks use `PatrolRunStore` and never write depth or annotated images. `metadata.json` records the dynamic overlay data consumed by the dashboard.

- [ ] **Step 6: Run tests and static checks**

Run: `PYTHONPATH=app pytest -q tests/unit/test_patrol_target_integration.py tests/integration/test_patrol_replay.py tests/unit/test_route_controller.py tests/unit/test_config_loader.py`; run `python3 -m compileall app`.
Expected: PASS; no test enables reverse motion without entering the target task.

- [ ] **Step 7: Commit**

```bash
git add app/wenshi_patrol/patrol_controller.py app/wenshi_patrol/vision/storage.py app/wenshi_patrol/vision/guard.py config/wenshi.yaml tests/unit/test_route_controller.py tests/unit/test_patrol_target_integration.py tests/integration/test_patrol_replay.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: integrate guarded rice target patrol flow"
```

### Task 6: Add Supervised Hardware Checkpoints and Failure Recovery Tests

**Files:**
- Modify: `scripts/check_environment.sh`
- Modify: `scripts/check_hardware_links.sh`
- Modify: `docs/SAFETY.md`
- Modify: `docs/OPERATIONS.md`
- Create: `tests/integration/test_supervised_motion_guards.py`

**Interfaces:**
- Preflight reports camera health, model availability, fixed viewpoint validation, and reverse warning without enabling reverse.
- `start`/`start loop` refuse stale AGV/JAKA/camera state and leave the AGV stopped on every failed precondition.

- [ ] **Step 1: Write offline guard tests**

Assert missing camera, missing model, invalid viewpoints, blocked/emergency AGV, stale camera, and target-loss paths all command stop and log a reason.

- [ ] **Step 2: Run tests and confirm failure where new checks are absent**

Run: `PYTHONPATH=app pytest -q tests/integration/test_supervised_motion_guards.py`
Expected: FAIL for the new target-task preflight cases.

- [ ] **Step 3: Implement only warning-level rear-radar reporting**

Keep `rear_radar_verified: false`; document that target alignment reverse is allowed only under the target-task hard guard and human supervision. Do not add a general `test back` bypass.

- [ ] **Step 4: Run all runtime tests**

Run: `PYTHONPATH=app pytest -q tests/unit tests/integration/test_camera_bridge_ros.py tests/integration/test_patrol_replay.py tests/integration/test_supervised_motion_guards.py`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_environment.sh scripts/check_hardware_links.sh docs/SAFETY.md docs/OPERATIONS.md tests/integration/test_supervised_motion_guards.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "test: add supervised patrol safety checkpoints"
```

## Plan Completion Check

Run `rg -n "TODO|TBD|placeholder|implement later" app/wenshi_patrol tests config`; expected no new output. Confirm a mocked full loop produces one run directory, one far/near pair per successful target, and a deterministic event trace from confirm through resume.
