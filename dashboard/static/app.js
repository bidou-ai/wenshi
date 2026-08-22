const state = {
  runs: [],
  run: null,
  selectedTargetId: null,
  filter: "all",
  adminToken: "",
  loadVersion: 0,
};

const $ = (id) => document.getElementById(id);

const RUN_STATUS = {
  running: { label: "巡检中", tone: "active" },
  finished: { label: "已完成", tone: "success" },
  completed: { label: "已完成", tone: "success" },
  stopped: { label: "已停止", tone: "warning" },
  terminated: { label: "已中止", tone: "warning" },
  failed: { label: "异常", tone: "danger" },
  error: { label: "异常", tone: "danger" },
  unknown: { label: "状态未知", tone: "neutral" },
};

const TARGET_STATUS = {
  created: { label: "已发现", tone: "neutral" },
  detected: { label: "已发现", tone: "neutral" },
  far_captured: { label: "远景完成", tone: "active" },
  approaching: { label: "正在抵近", tone: "active" },
  near_captured: { label: "采集完成", tone: "success" },
  near_failed: { label: "近景异常", tone: "danger" },
  failed: { label: "采集异常", tone: "danger" },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusInfo(status, target = false) {
  const normalized = String(status || "unknown").toLowerCase();
  const values = target ? TARGET_STATUS : RUN_STATUS;
  return values[normalized] || {
    label: status ? String(status) : "状态未知",
    tone: "neutral",
  };
}

function sideLabel(side) {
  const values = { left: "左侧", right: "右侧", center: "中间" };
  return values[String(side || "").toLowerCase()] || (side ? String(side) : "未记录");
}

function formatDate(value) {
  if (!value) return "时间未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function formatClock(value) {
  if (!value) return "--:--:--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function runDate(run) {
  if (run.created_at) return formatDate(run.created_at);
  const match = String(run.run_id || "").match(/^run_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
  if (!match) return "时间未记录";
  return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`;
}

function formatDistance(value) {
  if (value === null || value === undefined || value === "") return "未记录";
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(2)} m` : "未记录";
}

function formatQuality(quality) {
  if (quality === null || quality === undefined) return "未评分";
  if (typeof quality === "number") {
    const score = quality <= 1 ? quality * 100 : quality;
    return `${score.toFixed(0)} 分`;
  }
  if (typeof quality === "object") {
    const score = Number(quality.score);
    if (Number.isFinite(score)) {
      const normalized = score <= 1 ? score * 100 : score;
      return `${normalized.toFixed(0)} 分 · ${quality.ok ? "合格" : "需复核"}`;
    }
    if (typeof quality.ok === "boolean") return quality.ok ? "合格" : "需复核";
  }
  return String(quality);
}

function datasetNumber(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "";
}

function isIssue(target) {
  const status = String(target?.status || "").toLowerCase();
  return Boolean(target?.failure_reason) || status === "failed" || status === "near_failed";
}

function filterTargets(targets, filter) {
  const values = Array.isArray(targets) ? targets : [];
  if (filter === "complete") return values.filter((target) => target.near?.file || target.status === "near_captured");
  if (filter === "issues") return values.filter(isIssue);
  return values;
}

function adjacentTargetId(targets, currentId, direction) {
  const values = Array.isArray(targets) ? targets : [];
  const index = values.findIndex((target) => target.target_id === currentId);
  const nextIndex = index + Number(direction);
  if (index < 0 || nextIndex < 0 || nextIndex >= values.length) return null;
  return values[nextIndex].target_id;
}

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "请求失败");
  return value;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "请求失败");
  return value;
}

function setError(message = "") {
  const banner = $("errorBanner");
  banner.textContent = message;
  banner.hidden = !message;
}

function setSystemStatus(status, text) {
  const info = statusInfo(status);
  const element = $("systemStatus");
  element.className = `connection-state status-${info.tone}`;
  $("systemStatusText").textContent = text || info.label;
}

function updateRefreshTime() {
  $("lastRefresh").textContent = formatClock(new Date());
}

function renderRunSelector() {
  const selector = $("runSelector");
  if (!state.runs.length) {
    selector.innerHTML = '<option value="">暂无巡检记录</option>';
    selector.value = "";
    selector.disabled = true;
    return;
  }

  selector.disabled = false;
  selector.innerHTML = state.runs.map((run) => {
    const info = statusInfo(run.status);
    const count = Number(run.target_count) || 0;
    return `<option value="${escapeHtml(run.run_id)}">${escapeHtml(runDate(run))} · ${escapeHtml(info.label)} · ${count} 株</option>`;
  }).join("");
  selector.value = state.run?.run_id || state.runs[0].run_id;
}

function currentTargets() {
  return state.run?.targets || [];
}

function visibleTargets() {
  return filterTargets(currentTargets(), state.filter);
}

function renderSummary() {
  const targets = currentTargets();
  const nearCount = targets.filter((target) => target.near?.file).length;
  const issueCount = targets.filter(isIssue).length;
  const route = [...targets].reverse().find((target) => target.route_segment)?.route_segment || "未记录";
  const info = statusInfo(state.run?.status);

  $("overviewTitle").textContent = state.run ? runDate(state.run) : "等待巡检数据";
  $("summary").innerHTML = `
    <div><span>运行状态</span><strong class="value-${info.tone}">${escapeHtml(info.label)}</strong></div>
    <div><span>识别植株</span><strong>${targets.length} 株</strong></div>
    <div><span>近景完成</span><strong class="${nearCount === targets.length && targets.length ? "value-success" : ""}">${nearCount} 株</strong></div>
    <div><span>异常</span><strong class="${issueCount ? "value-danger" : "value-success"}">${issueCount} 项</strong></div>
    <div><span>当前路线段</span><strong>${escapeHtml(route)}</strong></div>`;
}

function updateFilterButtons() {
  document.querySelectorAll(".filter-button").forEach((button) => {
    const selected = button.dataset.filter === state.filter;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function targetRecordClass(target) {
  if (isIssue(target)) return "record-issue";
  if (target.near?.file || target.status === "near_captured") return "record-complete";
  if (target.far?.file) return "record-partial";
  return "record-pending";
}

function thumbnailMarkup(target) {
  const metadata = target.near?.file ? target.near : target.far;
  const label = target.near?.file ? "近景" : "远景";
  if (!metadata?.file) {
    return `<span class="record-thumbnail-placeholder">${escapeHtml(target.target_id)}</span>`;
  }
  return `
    <img src="${mediaUrl(target.target_id, metadata.file)}" alt="${escapeHtml(target.target_id)} ${label}缩略图" loading="lazy">
    <span class="thumbnail-type">${label}</span>`;
}

function renderTargets() {
  const allTargets = currentTargets();
  const targets = visibleTargets();
  updateFilterButtons();
  $("targetCount").textContent = state.filter === "all" ? `${allTargets.length} 株` : `${targets.length} / ${allTargets.length} 株`;

  if (!targets.length) {
    const message = allTargets.length ? "当前筛选条件下没有目标" : "本次巡检尚未识别到水稻目标";
    $("targets").innerHTML = `<div class="navigator-empty">${message}</div>`;
    renderNavigationControls();
    return;
  }

  $("targets").innerHTML = targets.map((target, index) => {
    const selected = target.target_id === state.selectedTargetId;
    const info = statusInfo(target.status, true);
    const alert = isIssue(target)
      ? `<p class="record-alert">${escapeHtml(target.failure_reason || info.label)}</p>`
      : "";
    return `
      <button class="plant-record ${targetRecordClass(target)}${selected ? " selected" : ""}" data-id="${escapeHtml(target.target_id)}" type="button" aria-pressed="${selected}">
        <span class="record-thumbnail">${thumbnailMarkup(target)}</span>
        <span class="record-body">
          <span class="record-primary">
            <strong>${escapeHtml(target.target_id)}</strong>
            <span class="record-sequence">${String(index + 1).padStart(2, "0")}</span>
          </span>
          <span class="record-secondary">${escapeHtml(sideLabel(target.side))} · ${escapeHtml(target.route_segment || "路线未记录")}</span>
          <span class="record-status-line"><span class="mini-status status-${info.tone}">${escapeHtml(info.label)}</span></span>
          ${alert}
        </span>
        <span class="record-indicator" aria-hidden="true"></span>
      </button>`;
  }).join("");

  document.querySelectorAll(".plant-record").forEach((button) => {
    button.addEventListener("click", () => {
      const target = currentTargets().find((item) => item.target_id === button.dataset.id);
      if (target) renderTarget(target);
    });
  });
  renderNavigationControls();
}

function renderNavigationControls() {
  const targets = visibleTargets();
  const index = targets.findIndex((target) => target.target_id === state.selectedTargetId);
  $("targetPosition").textContent = index >= 0 ? `${index + 1} / ${targets.length}` : `0 / ${targets.length}`;
  $("previousTarget").disabled = index <= 0;
  $("nextTarget").disabled = index < 0 || index >= targets.length - 1;
}

function mediaUrl(targetId, filename) {
  if (!filename || !state.run) return "";
  return `/media/${encodeURIComponent(state.run.run_id)}/${encodeURIComponent(targetId)}/${encodeURIComponent(filename)}`;
}

function photoPaneMarkup(target, key) {
  const far = key === "far";
  const metadata = target[key] || {};
  const filename = metadata.file;
  const bbox = metadata.bbox || {};
  const title = far ? "远景 · 定位影像" : "近景 · 花期复核";
  const paneClass = far ? "photo-pane-far" : "photo-pane-near";
  const index = far ? "A" : "B";
  const headerState = filename ? "已保存" : "未保存";
  const footerLabel = far ? "路线段位置" : "图像质量";
  const footerValue = far
    ? `${target.route_segment || "路线未记录"} · ${sideLabel(target.side)}`
    : formatQuality(metadata.quality);
  const content = filename
    ? `<img src="${mediaUrl(target.target_id, filename)}" alt="${escapeHtml(target.target_id)} ${far ? "远景" : "近景"}" data-cx="${datasetNumber(bbox.cx)}" data-cy="${datasetNumber(bbox.cy)}" data-bw="${datasetNumber(bbox.width)}" data-bh="${datasetNumber(bbox.height)}"><span class="bbox" hidden></span><button class="zoom-button" type="button" data-image-src="${mediaUrl(target.target_id, filename)}" data-image-title="${escapeHtml(target.target_id)} ${title}">查看原图</button>`
    : `<div class="image-placeholder">${far ? "远景" : "近景"}未保存</div>`;

  return `
    <section class="photo-pane ${paneClass}" aria-label="${title}">
      <header class="photo-pane-header">
        <div><span class="photo-index">${index}</span><strong>${title}</strong></div>
        <span>${headerState}${filename ? ` · ${escapeHtml(filename)}` : ""}</span>
      </header>
      <div class="image-stage">${content}</div>
      <footer class="photo-pane-footer"><span>${footerLabel}</span><strong>${escapeHtml(footerValue)}</strong></footer>
    </section>`;
}

function applyBoxes() {
  document.querySelectorAll(".image-stage img").forEach((image) => {
    const update = () => {
      const raw = [image.dataset.cx, image.dataset.cy, image.dataset.bw, image.dataset.bh];
      const box = image.parentElement.querySelector(".bbox");
      if (!box || raw.some((value) => value === "") || !image.naturalWidth || !image.naturalHeight) {
        if (box) box.hidden = true;
        return;
      }

      const [cx, cy, width, height] = raw.map(Number);
      if (![cx, cy, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        box.hidden = true;
        return;
      }

      box.hidden = false;
      box.style.left = `${(cx - width / 2) / image.naturalWidth * 100}%`;
      box.style.top = `${(cy - height / 2) / image.naturalHeight * 100}%`;
      box.style.width = `${width / image.naturalWidth * 100}%`;
      box.style.height = `${height / image.naturalHeight * 100}%`;
    };

    if (image.complete) update();
    image.addEventListener("load", update);
  });
}

function applyZoomButtons() {
  document.querySelectorAll(".zoom-button").forEach((button) => {
    button.addEventListener("click", () => {
      $("imageDialogTitle").textContent = button.dataset.imageTitle;
      $("imageDialogImage").src = button.dataset.imageSrc;
      $("imageDialog").showModal();
    });
  });
}

function renderEvidence(target) {
  const quality = target.near?.quality ?? target.far?.quality;
  $("evidence").innerHTML = `
    <div><dt>目标编号</dt><dd>${escapeHtml(target.target_id)}</dd></div>
    <div><dt>拍摄时间</dt><dd>${escapeHtml(formatDate(target.updated_at || target.created_at))}</dd></div>
    <div><dt>路线段位置</dt><dd>${escapeHtml(target.route_segment || "未记录")} · ${escapeHtml(formatDistance(target.along_track_m))}</dd></div>
    <div><dt>图像质量</dt><dd>${escapeHtml(formatQuality(quality))}</dd></div>
    <div><dt>远景文件</dt><dd>${escapeHtml(target.far?.file || "未保存")}</dd></div>
    <div><dt>近景文件</dt><dd>${escapeHtml(target.near?.file || "未保存")}</dd></div>
    <div class="evidence-wide"><dt>异常说明</dt><dd>${escapeHtml(target.failure_reason || "无")}</dd></div>`;
}

function renderTarget(target) {
  state.selectedTargetId = target.target_id;
  const info = statusInfo(target.status, true);
  $("inspectionTitle").textContent = `${target.target_id} · 水稻植株`;
  $("inspectionStatus").className = `status-tag status-${info.tone}`;
  $("inspectionStatus").textContent = info.label;
  $("inspectionLocation").textContent = `${target.route_segment || "路线段未记录"} · ${sideLabel(target.side)} · 前后顺序 ${formatDistance(target.along_track_m)}`;
  $("photoComparison").innerHTML = `${photoPaneMarkup(target, "far")}${photoPaneMarkup(target, "near")}`;
  renderEvidence(target);
  $("adminTools").hidden = !state.adminToken;
  renderTargets();
  applyBoxes();
  applyZoomButtons();

  const selectedRecord = [...document.querySelectorAll(".plant-record")]
    .find((record) => record.dataset.id === target.target_id);
  if (selectedRecord) selectedRecord.scrollIntoView({ block: "nearest" });
}

function renderInspectionEmpty(message = "选择一株水稻开始复核") {
  state.selectedTargetId = null;
  $("inspectionTitle").textContent = "目标详情";
  $("inspectionStatus").className = "status-tag status-neutral";
  $("inspectionStatus").textContent = "等待选择";
  $("inspectionLocation").textContent = message;
  $("photoComparison").innerHTML = `
    <section class="photo-pane photo-pane-far" aria-label="远景定位影像">
      <header class="photo-pane-header"><div><span class="photo-index">A</span><strong>远景 · 定位影像</strong></div><span>等待目标</span></header>
      <div class="image-stage"><div class="image-placeholder">远景未加载</div></div>
      <footer class="photo-pane-footer"><span>路线段位置</span><strong>--</strong></footer>
    </section>
    <section class="photo-pane photo-pane-near" aria-label="近景花期复核影像">
      <header class="photo-pane-header"><div><span class="photo-index">B</span><strong>近景 · 花期复核</strong></div><span>等待目标</span></header>
      <div class="image-stage"><div class="image-placeholder">近景未加载</div></div>
      <footer class="photo-pane-footer"><span>图像质量</span><strong>--</strong></footer>
    </section>`;
  $("evidence").innerHTML = `
    <div><dt>目标编号</dt><dd>--</dd></div>
    <div><dt>拍摄时间</dt><dd>--</dd></div>
    <div><dt>路线段位置</dt><dd>--</dd></div>
    <div><dt>图像质量</dt><dd>--</dd></div>
    <div class="evidence-wide"><dt>异常说明</dt><dd>无</dd></div>`;
  $("adminTools").hidden = true;
  renderNavigationControls();
}

function selectAdjacentTarget(direction) {
  const targets = visibleTargets();
  const id = adjacentTargetId(targets, state.selectedTargetId, direction);
  if (!id) return;
  const target = currentTargets().find((item) => item.target_id === id);
  if (target) renderTarget(target);
}

function applyFilter(filter) {
  state.filter = filter;
  const targets = visibleTargets();
  const selected = targets.find((target) => target.target_id === state.selectedTargetId) || targets[0];
  if (selected) renderTarget(selected);
  else {
    renderTargets();
    renderInspectionEmpty("当前筛选条件下没有可复核目标");
  }
}

function renderNoRuns() {
  state.run = null;
  state.selectedTargetId = null;
  $("selectedRun").textContent = "暂无批次";
  setSystemStatus("unknown", "等待巡检数据");
  renderRunSelector();
  renderSummary();
  renderTargets();
  renderInspectionEmpty("后台已正常启动，等待巡检数据");
}

async function loadRun(id, preferredTargetId = null) {
  const version = ++state.loadVersion;
  setError();
  $("selectedRun").textContent = id;
  setSystemStatus("unknown", "正在读取");

  try {
    const run = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (version !== state.loadVersion) return;

    state.run = run;
    const targets = filterTargets(run.targets || [], state.filter);
    state.selectedTargetId = preferredTargetId && targets.some((item) => item.target_id === preferredTargetId)
      ? preferredTargetId
      : targets[0]?.target_id || null;

    $("selectedRun").textContent = run.run_id;
    setSystemStatus(run.status);
    renderRunSelector();
    renderSummary();
    renderTargets();

    const selected = (run.targets || []).find((item) => item.target_id === state.selectedTargetId);
    if (selected) renderTarget(selected);
    else renderInspectionEmpty(targets.length ? "选择一株水稻开始复核" : "本次巡检没有符合条件的目标");
  } catch (error) {
    if (version !== state.loadVersion) return;
    setSystemStatus("error", "数据读取异常");
    setError(`无法读取巡检批次：${error.message}`);
  }
}

async function adminAction(path, body, message, confirmation) {
  if (!state.adminToken) {
    $("adminStatus").textContent = "请先登录管理员 PIN";
    $("adminDialog").showModal();
    return;
  }
  if (confirmation && !window.confirm(confirmation)) return;

  try {
    await post(path, { ...body, token: state.adminToken });
    $("adminStatus").textContent = message;
    await refresh(true);
  } catch (error) {
    $("adminStatus").textContent = error.message;
    setError(`管理员操作失败：${error.message}`);
  }
}

async function login() {
  try {
    const value = await post("/api/admin/auth", { pin: $("pin").value });
    state.adminToken = value.token;
    $("adminStatus").textContent = "管理员已登录";
    $("admin").textContent = "管理员已登录";
    $("pin").value = "";
    const selected = currentTargets().find((item) => item.target_id === state.selectedTargetId);
    if (selected) renderTarget(selected);
    window.setTimeout(() => $("adminDialog").close(), 350);
  } catch (error) {
    $("adminStatus").textContent = error.message;
  }
}

async function refresh(silent = false) {
  const button = $("refresh");
  const previousRunId = state.run?.run_id;
  const previousTargetId = state.selectedTargetId;
  button.disabled = true;
  if (!silent) setError();

  try {
    state.runs = (await api("/api/runs")).runs || [];
    renderRunSelector();
    if (!state.runs.length) {
      renderNoRuns();
    } else {
      const runId = state.runs.some((run) => run.run_id === previousRunId)
        ? previousRunId
        : state.runs[0].run_id;
      await loadRun(runId, previousTargetId);
    }
    updateRefreshTime();
  } catch (error) {
    setSystemStatus("error", "后台连接异常");
    setError(`无法连接巡检后台：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

$("runSelector").addEventListener("change", (event) => {
  if (event.target.value) loadRun(event.target.value);
});
$("refresh").addEventListener("click", () => refresh());
$("previousTarget").addEventListener("click", () => selectAdjacentTarget(-1));
$("nextTarget").addEventListener("click", () => selectAdjacentTarget(1));
document.querySelectorAll(".filter-button").forEach((button) => {
  button.addEventListener("click", () => applyFilter(button.dataset.filter));
});
$("closeImage").addEventListener("click", () => $("imageDialog").close());
$("imageDialog").addEventListener("click", (event) => {
  if (event.target === $("imageDialog")) $("imageDialog").close();
});
$("admin").addEventListener("click", () => {
  $("adminStatus").textContent = state.adminToken ? "管理员已登录，可执行当前目标管理操作。" : "";
  $("adminDialog").showModal();
});
$("login").addEventListener("click", (event) => {
  event.preventDefault();
  login();
});
$("deleteTarget").addEventListener("click", () => {
  if (!state.run || !state.selectedTargetId) return;
  adminAction(
    "/api/admin/delete",
    { run_id: state.run.run_id, target_id: state.selectedTargetId },
    "目标已移入回收区",
    `确认删除目标 ${state.selectedTargetId}？此操作会将目标移入回收区。`
  );
});
$("resetDedupe").addEventListener("click", () => {
  if (!state.run) return;
  adminAction(
    "/api/admin/reset-dedupe",
    { run_id: state.run.run_id },
    "已写入去重重置标记",
    "确认重置本次巡检的去重记录？"
  );
});

refresh();
window.setInterval(() => refresh(true), 30000);
