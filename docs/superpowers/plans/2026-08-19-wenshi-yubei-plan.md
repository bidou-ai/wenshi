# Wenshi Yubei Preparation Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create an independent, removable preparation toolbox for camera/network/device checks, RGB dataset capture, browser annotation, YOLO dataset validation/training, model publishing, and eight-point arm teaching.

**Architecture:** `yubei` owns its CLI parsing, HTTP camera client, minimal read-only device probes, dataset session schema, annotation server, and teaching file operations. It may read configuration and communicate with a device for an explicitly requested test, but it never imports or starts the formal Wenshi patrol node. Dataset sessions live under `yubei/data/`; published model files are copied to formal `models/` only through an explicit command.

**Tech Stack:** Python 3.10 standard library, OpenCV/NumPy for image handling, optional Ultralytics for training, HTML/CSS/vanilla JavaScript for annotation.

**Spec:** `docs/superpowers/specs/2026-08-19-wenshi-yubei-demo-design-zh.md`

## Global Constraints

- Classes are exactly `rice=0` and `flower=1`; first dataset may contain only rice labels.
- Dataset capture saves RGB JPG only; no depth or patrol metadata is written into a training session.
- Enter saves one image, `q` ends the session; manual arm movement may happen between captures.
- A dataset image may contain multiple complete-plant boxes.
- Ambiguous heavy overlap is skipped into `ambiguous/`, never merged into one rice box.
- Every formal-viewpoint publish makes a timestamped backup and validates before replacing the formal file.
- No yubei module imports `wenshi_patrol`.

### Task 1: Create Yubei Paths, Schemas, and CLI Conventions

**Files:**
- Create: `yubei/__init__.py`
- Create: `yubei/paths.py`
- Create: `yubei/schemas.py`
- Create: `yubei/cli.py`
- Create: `tests/unit/test_yubei_paths.py`
- Create: `tests/unit/test_yubei_schemas.py`

**Interfaces:**
- `SessionPaths.create(root: Path, prefix: str = "dataset") -> SessionPaths`
- `SessionPaths.images_dir`, `labels_dir`, `ambiguous_dir`, `manifest_path` properties
- `save_json_atomic(path: Path, value: dict) -> None`
- `load_json(path: Path) -> dict`
- `DatasetManifest.add_image(filename: str, width: int, height: int, status: str) -> None`
- `DatasetManifest.write(path: Path) -> None`

- [ ] **Step 1: Write failing tests**

```python
def test_session_paths_create_one_session_tree(tmp_path):
    session = SessionPaths.create(tmp_path)
    assert session.images_dir.is_dir()
    assert session.labels_dir.is_dir()
    assert session.ambiguous_dir.is_dir()
    assert session.manifest_path.name == "manifest.json"

def test_manifest_rejects_unknown_status(tmp_path):
    manifest = DatasetManifest()
    with pytest.raises(ValueError, match="status"):
        manifest.add_image("a.jpg", 1280, 720, "unknown")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_paths.py tests/unit/test_yubei_schemas.py`
Expected: FAIL because the modules and types do not exist.

- [ ] **Step 3: Implement the minimal path and manifest types**

Use `Path.mkdir(parents=True, exist_ok=False)` for the session root, write JSON with UTF-8 and a temporary sibling file, and allow only `captured`, `skipped`, and `ambiguous` image statuses. Keep all paths resolved below the supplied root.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_paths.py tests/unit/test_yubei_schemas.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add yubei tests/unit/test_yubei_paths.py tests/unit/test_yubei_schemas.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add yubei dataset session primitives"
```

### Task 2: Add Camera, Network, and Read-Only Device Diagnostics

**Files:**
- Create: `yubei/http_camera.py`
- Create: `yubei/camera_check.py`
- Create: `yubei/network_check.py`
- Create: `yubei/device_check.py`
- Create: `yubei/device_protocol.py`
- Create: `tests/unit/test_yubei_camera.py`
- Create: `tests/unit/test_yubei_network.py`
- Create: `tests/unit/test_yubei_device_check.py`

**Interfaces:**
- `HttpCameraClient.health() -> dict`
- `HttpCameraClient.frame() -> CameraFrame`
- `CameraFrame.color: np.ndarray`, `CameraFrame.depth: np.ndarray`, `CameraFrame.seq: int`, `CameraFrame.intrinsics: dict`
- `probe_tcp(host: str, port: int, timeout_s: float) -> ProbeResult`
- `probe_camera(url: str, samples: int, interval_s: float) -> dict`
- `read_only_device_report(agv_host: str, jaka_host: str) -> dict`

- [ ] **Step 1: Write tests with fake HTTP responses and sockets**

```python
def test_camera_client_decodes_color_and_depth(monkeypatch):
    client = HttpCameraClient("http://camera.test:18080", opener=FakeOpener(frame_packet))
    frame = client.frame()
    assert frame.color.shape == (720, 1280, 3)
    assert frame.depth.dtype == np.uint16
    assert frame.seq == 7

def test_probe_tcp_reports_refused_without_raising(monkeypatch):
    result = probe_tcp("127.0.0.1", 1, 0.01)
    assert result.ok is False
    assert result.error
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_camera.py tests/unit/test_yubei_network.py tests/unit/test_yubei_device_check.py`
Expected: FAIL due to missing client and probe functions.

- [ ] **Step 3: Implement HTTP and diagnostic modules**

Use `urllib.request` with an empty proxy handler, base64 decode through `cv2.imdecode`, and report health, frame sequence, resolution, depth dtype, decode time, sequence gaps, stale frames, and HTTP errors. `camera_check.py --preview` opens RGB and colorized depth windows; `q` closes it. `network_check.py` tests the configured AGV/JAKA/camera endpoints and prints whether the workstation has a default route. `device_check.py` only connects, requests status, and disconnects; it exposes no reverse command.

- [ ] **Step 4: Run tests and CLI smoke checks**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_camera.py tests/unit/test_yubei_network.py tests/unit/test_yubei_device_check.py` and `python3 yubei/camera_check.py --help`.
Expected: PASS and help text lists `--url`, `--samples`, `--preview`, and `--timeout`.

- [ ] **Step 5: Commit**

```bash
git add yubei/http_camera.py yubei/camera_check.py yubei/network_check.py yubei/device_check.py yubei/device_protocol.py tests/unit/test_yubei_camera.py tests/unit/test_yubei_network.py tests/unit/test_yubei_device_check.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add yubei diagnostics"
```

### Task 3: Implement One-Enter-Per-Image Dataset Capture

**Files:**
- Create: `yubei/dataset_capture.py`
- Create: `yubei/capture_ui.py`
- Create: `tests/unit/test_yubei_capture.py`
- Modify: `yubei/README.md`

**Interfaces:**
- `CaptureSession(camera: HttpCameraClient, paths: SessionPaths).capture_one() -> Path`
- `CaptureSession.run(input_stream, output_stream, preview: bool) -> int`
- `capture_image(frame: CameraFrame, path: Path, jpeg_quality: int = 95) -> dict`

- [ ] **Step 1: Write tests for Enter, q, stale-frame rejection, and manifest updates**

```python
def test_capture_session_enter_saves_exactly_one_jpg(tmp_path, fake_camera):
    session = CaptureSession(fake_camera, SessionPaths.create(tmp_path))
    count = session.run(io.StringIO("\nq\n"), io.StringIO(), preview=False)
    assert count == 1
    assert len(list(session.paths.images_dir.glob("*.jpg"))) == 1
    assert json.loads(session.paths.manifest_path.read_text())["images"][0]["status"] == "captured"
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_capture.py`
Expected: FAIL because the capture session is absent.

- [ ] **Step 3: Implement the capture loop**

Create one session directory at startup, show the latest RGB frame when `--preview` is enabled, fetch a fresh frame only when Enter is received, save one native 1280x720 JPG with quality 95, update the manifest, and never save depth. Print a prompt that explicitly permits manual arm movement between Enter presses. `q` exits cleanly and writes `session_summary.json`.

- [ ] **Step 4: Test and manually smoke-test**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_capture.py` and `python3 yubei/dataset_capture.py --help`.
Expected: PASS; help lists `--url`, `--output`, `--preview`, and `--jpeg-quality`.

- [ ] **Step 5: Commit**

```bash
git add yubei/dataset_capture.py yubei/capture_ui.py yubei/README.md tests/unit/test_yubei_capture.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add interactive yubei dataset capture"
```

### Task 4: Build the Browser Annotation Tool

**Files:**
- Create: `yubei/label_server.py`
- Create: `yubei/label_ui/index.html`
- Create: `yubei/label_ui/app.js`
- Create: `yubei/label_ui/style.css`
- Create: `tests/unit/test_yubei_labels.py`
- Modify: `yubei/ANNOTATION_GUIDE.md`

**Interfaces:**
- `LabelStore.list_images() -> list[dict]`
- `LabelStore.load(image_name: str) -> dict`
- `LabelStore.save(image_name: str, boxes: list[dict], status: str) -> None`
- `LabelStore.export_yolo(image_name: str) -> Path`
- HTTP `GET /api/images`, `GET /api/image/<name>`, `GET /api/labels/<name>`, `PUT /api/labels/<name>`, `GET /media/<name>`

- [ ] **Step 1: Write API tests**

Test path traversal rejection, normalized bbox validation, `rice`/`flower` class validation, ambiguous status, and YOLO normalized export.

- [ ] **Step 2: Run the tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_labels.py`
Expected: FAIL because no label store or server exists.

- [ ] **Step 3: Implement the store and HTTP API**

Use `ThreadingHTTPServer`, resolve every requested filename below the session root, store labels as `<stem>.json`, and atomically write YOLO TXT files. The browser UI must support previous/next, image zoom, draw/move/delete boxes, class selection, undo/redo, copy previous boxes, auto-save, unlabelled/ambiguous filters, and progress. Heavy overlap is marked `ambiguous` instead of merged.

- [ ] **Step 4: Run tests and browser fixture smoke test**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_labels.py`; start `python3 yubei/label_server.py --session <fixture>` and verify the index loads and one label round-trips through the API.

- [ ] **Step 5: Commit**

```bash
git add yubei/label_server.py yubei/label_ui yubei/ANNOTATION_GUIDE.md tests/unit/test_yubei_labels.py
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add local yubei annotation tool"
```

### Task 5: Validate, Train, and Explicitly Publish YOLO Models

**Files:**
- Create: `yubei/dataset_validate.py`
- Create: `yubei/train_yolo.py`
- Create: `yubei/publish_model.py`
- Create: `yubei/yolo_data.yaml`
- Create: `tests/unit/test_yubei_dataset.py`
- Create: `TRAINING.md`
- Modify: `models/README.md`
- Modify: `requirements-ubuntu.txt`

**Interfaces:**
- `validate_dataset(session: Path) -> ValidationReport`
- `write_yolo_dataset_yaml(path: Path, train: Path, val: Path) -> None`
- `run_training(args: argparse.Namespace) -> int`
- `publish_model(source: Path, formal_models_dir: Path, metadata: dict) -> Path`

- [ ] **Step 1: Write validation and publish tests**

Cover missing labels, invalid class IDs, boxes outside `[0,1]`, image/label mismatches, deterministic train/val split, SHA256 recording, and publish refusal when the source is not a `.pt` file.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_dataset.py`
Expected: FAIL because the validation and publishing functions do not exist.

- [ ] **Step 3: Implement offline validation and optional Ultralytics training**

Use a deterministic seed, write class names `rice` and `flower`, allow `--classes rice` for the first run, and fail with an actionable install message if `ultralytics` is unavailable. Training output goes under `yubei/training/<timestamp>/`; never overwrite formal models from the trainer.

- [ ] **Step 4: Implement explicit model publish**

Copy to `models/rice_demo.pt` only after checking file size, SHA256, and a sidecar JSON containing class names, source run, training date, and validation metrics. Replace an existing model only after moving its old sidecar to `models/archive/`.

- [ ] **Step 5: Run tests and documentation checks**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_dataset.py`; run `python3 yubei/dataset_validate.py --help`, `python3 yubei/train_yolo.py --help`, and `python3 yubei/publish_model.py --help`.

- [ ] **Step 6: Commit**

```bash
git add yubei/dataset_validate.py yubei/train_yolo.py yubei/publish_model.py yubei/yolo_data.yaml tests/unit/test_yubei_dataset.py TRAINING.md models/README.md requirements-ubuntu.txt
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add yubei dataset validation and model publishing"
```

### Task 6: Prepare Eight-Point Arm Teaching and Verification

**Files:**
- Create: `yubei/teach_protocol.py`
- Create: `yubei/teach_viewpoints.py`
- Create: `yubei/viewpoint_verify.py`
- Create: `tests/unit/test_yubei_viewpoints.py`
- Modify: `config/viewpoints.json`
- Modify: `config/fixed_targets.json`
- Modify: `config/wenshi.yaml`

**Interfaces:**
- `VIEWPOINT_NAMES = ("home_safe", "camera", "camera_left", "camera_right", "left_pre", "left_photo", "right_pre", "right_photo")`
- `TeachingClient.read_joint() -> list[float]`
- `TeachingSession.save(name: str, joint: list[float], tcp: list[float] | None) -> Path`
- `verify_viewpoints(path: Path) -> VerificationReport`
- `publish_viewpoints(staged: Path, formal: Path, backup_dir: Path) -> Path`

- [ ] **Step 1: Write tests for exact names, backup, step limits, and sequence order**

The tests use a fake read-only JSON TCP server and assert that `home_safe` is required, both side sequences are checked, and publish creates a timestamped backup before replacement.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_viewpoints.py`
Expected: FAIL because the standalone teaching client and verification report do not exist.

- [ ] **Step 3: Implement staged teaching**

Do not auto-power or auto-enable the robot. Require an explicit CLI command per read/save/goto/snapshot action; use the existing formal safe TCP read/move protocol only. Save all eight points to a staged file and retain previous staged copies.

- [ ] **Step 4: Implement verification and explicit publish**

Check all point names, six joints, max adjacent joint step, side path order, return corridor metadata, and JSON schema. Publish only after `--confirm`, copy the old formal file to `yubei/backups/<timestamp>/`, and print the exact destination.

- [ ] **Step 5: Run tests and CLI smoke checks**

Run: `PYTHONPATH=. pytest -q tests/unit/test_yubei_viewpoints.py`; run `python3 yubei/teach_viewpoints.py --help` and `python3 yubei/viewpoint_verify.py --help`.

- [ ] **Step 6: Commit**

```bash
git add yubei/teach_protocol.py yubei/teach_viewpoints.py yubei/viewpoint_verify.py tests/unit/test_yubei_viewpoints.py config/viewpoints.json config/fixed_targets.json config/wenshi.yaml
git -c user.name='Wenshi Maintainer' -c user.email='wenshi@localhost' commit -m "feat: add yubei viewpoint teaching tools"
```

## Plan Completion Check

Run `rg -n "TODO|TBD|placeholder|implement later" yubei TRAINING.md yubei/ANNOTATION_GUIDE.md`; expected no output. Run `PYTHONPATH=. pytest -q tests/unit/test_yubei_*.py` and verify no test imports `wenshi_patrol` from a yubei module.
