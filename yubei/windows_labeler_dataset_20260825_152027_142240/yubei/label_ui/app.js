const state = {
  images: [], filtered: [], index: -1, current: null, boxes: [], selected: -1,
  activeClass: 'rice', undo: [], redo: [], previous: null, drag: null,
  preview: null, status: null, image: null, zoom: 'fit',
};
const $ = (id) => document.getElementById(id);
const tagNames = { flower: '开花', rice: '水稻', neutral: '普通' };
const qualityNames = { blur: '模糊', low_contrast: '对比度低', underexposed: '欠曝', overexposed: '过曝' };

async function api(path, options) {
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function updateProgress() {
  const labelled = state.filtered.filter((item) => item.status === 'labelled').length;
  const flowers = state.filtered.filter((item) => item.capture_tag === 'flower').length;
  $('progress').textContent = `${labelled} / ${state.filtered.length} 已标注 · ${flowers} 张开花批次`;
}

function matchesFilter(item, filter) {
  if (filter === 'flower' || filter === 'rice') return item.capture_tag === filter;
  return filter === 'all' || item.status === filter;
}

function applyFilter() {
  const filter = $('filter').value;
  const currentName = state.current && state.current.name;
  state.filtered = state.images.filter((item) => matchesFilter(item, filter));
  if (!state.filtered.length) {
    state.index = -1; state.current = null; renderList(); updateProgress(); renderCanvas(); return;
  }
  const currentIndex = currentName ? state.filtered.findIndex((item) => item.name === currentName) : -1;
  state.index = currentIndex >= 0 ? currentIndex : Math.max(0, Math.min(state.index, state.filtered.length - 1));
  renderList(); updateProgress(); loadCurrent();
}

function renderList() {
  $('image-list').innerHTML = state.filtered.map((item, index) => {
    const warning = item.duplicate_of ? ' · 重复' : (item.quality && !item.quality.ok ? ' · 质量提醒' : '');
    return `<button class="image-item ${index === state.index ? 'selected' : ''}" data-index="${index}">
      <span>${item.name}<small>${tagNames[item.capture_tag] || '普通'}${warning}</small></span>
      <small>${item.status}<br>${item.box_count}框</small></button>`;
  }).join('');
  document.querySelectorAll('.image-item').forEach((button) => {
    button.onclick = () => { state.index = Number(button.dataset.index); loadCurrent(); };
  });
}

async function loadCurrent() {
  if (state.index < 0 || !state.filtered[state.index]) return;
  state.current = state.filtered[state.index];
  const value = await api(`/api/labels/${encodeURIComponent(state.current.name)}`);
  state.boxes = value.boxes || [];
  state.status = value.status || 'unlabelled';
  state.selected = -1; state.undo = []; state.redo = []; state.drag = null; state.preview = null;
  const capture = value.capture || {};
  const qualityReasons = ((capture.quality && capture.quality.reasons) || []).map((reason) => qualityNames[reason] || reason);
  $('capture-info').textContent = `当前照片批次：${tagNames[capture.capture_tag] || '普通'}${capture.seq ? ` · 相机帧 ${capture.seq}` : ''}${qualityReasons.length ? ` · ${qualityReasons.join('、')}` : ''}${capture.duplicate_of ? ` · 相似于 ${capture.duplicate_of}` : ''}`;
  state.image = null; imageElement(); renderList(); renderCanvas();
}

function renderCanvas() {
  const canvas = $('canvas'); const empty = $('empty');
  if (!state.current || !state.image) { canvas.width = canvas.height = 0; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  const image = state.image;
  const fitScale = Math.min(1, (canvas.parentElement.clientWidth - 20) / image.naturalWidth, (window.innerHeight - 250) / image.naturalHeight);
  const scale = state.zoom === 'fit' ? fitScale : Number(state.zoom);
  canvas.width = image.naturalWidth * scale; canvas.height = image.naturalHeight * scale; canvas.dataset.scale = scale;
  const context = canvas.getContext('2d'); context.drawImage(image, 0, 0, canvas.width, canvas.height);
  state.boxes.forEach((box, index) => {
    const color = index === state.selected ? '#ffcf54' : (box.class_name === 'rice' ? '#49d3a6' : '#ff9278');
    context.strokeStyle = color; context.lineWidth = index === state.selected ? 3 : 2;
    context.strokeRect(box.x * scale, box.y * scale, box.width * scale, box.height * scale);
    context.fillStyle = color; context.font = 'bold 13px system-ui';
    context.fillText(`${box.class_name} ${index + 1}`, box.x * scale + 4, box.y * scale + 15);
  });
  if (state.preview) {
    context.strokeStyle = '#ffffff'; context.setLineDash([6, 4]); context.lineWidth = 2;
    context.strokeRect(state.preview.x * scale, state.preview.y * scale, state.preview.width * scale, state.preview.height * scale);
    context.setLineDash([]);
  }
}

function imageElement() {
  if (!state.current) return;
  const image = new Image();
  image.onload = () => { state.image = image; renderCanvas(); };
  image.onerror = () => { state.image = null; renderCanvas(); };
  image.src = `/media/${encodeURIComponent(state.current.name)}`;
}

function pushUndo() { state.undo.push(JSON.stringify(state.boxes)); state.redo = []; }
function imagePoint(event) {
  const rect = $('canvas').getBoundingClientRect(); const scale = Number($('canvas').dataset.scale || 1);
  return { x: Math.max(0, (event.clientX - rect.left) / scale), y: Math.max(0, (event.clientY - rect.top) / scale) };
}
function hitBox(point) {
  for (let i = state.boxes.length - 1; i >= 0; i -= 1) {
    const box = state.boxes[i];
    if (point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height) return i;
  }
  return -1;
}
function clampBox(box) {
  if (!state.image) return box;
  box.x = Math.max(0, Math.min(box.x, state.image.naturalWidth - box.width));
  box.y = Math.max(0, Math.min(box.y, state.image.naturalHeight - box.height));
  return box;
}
function finishDrag(point) {
  if (!state.drag) return;
  const drag = state.drag; state.drag = null; state.preview = null;
  if (drag.mode === 'draw') {
    const box = { class_name: state.activeClass, x: Math.min(drag.start.x, point.x), y: Math.min(drag.start.y, point.y), width: Math.abs(point.x - drag.start.x), height: Math.abs(point.y - drag.start.y) };
    if (box.width >= 4 && box.height >= 4) { pushUndo(); state.boxes.push(clampBox(box)); state.selected = state.boxes.length - 1; }
  } else if (drag.changed) { state.undo.push(drag.before); state.redo = []; }
  renderCanvas();
}

$('canvas').addEventListener('mousedown', (event) => {
  const point = imagePoint(event); const hit = hitBox(point);
  if (hit >= 0) {
    const box = state.boxes[hit]; state.selected = hit;
    state.drag = { mode: 'move', index: hit, offsetX: point.x - box.x, offsetY: point.y - box.y, changed: false, before: JSON.stringify(state.boxes) };
  } else { state.selected = -1; state.drag = { mode: 'draw', start: point }; }
  renderCanvas();
});
$('canvas').addEventListener('mousemove', (event) => {
  if (!state.drag) return;
  const point = imagePoint(event);
  if (state.drag.mode === 'draw') state.preview = { x: Math.min(state.drag.start.x, point.x), y: Math.min(state.drag.start.y, point.y), width: Math.abs(point.x - state.drag.start.x), height: Math.abs(point.y - state.drag.start.y) };
  else { const box = state.boxes[state.drag.index]; box.x = point.x - state.drag.offsetX; box.y = point.y - state.drag.offsetY; clampBox(box); state.drag.changed = true; }
  renderCanvas();
});
$('canvas').addEventListener('mouseup', (event) => finishDrag(imagePoint(event)));
$('canvas').addEventListener('mouseleave', () => { if (state.drag && state.drag.mode === 'draw') { state.preview = null; renderCanvas(); } });

document.querySelectorAll('.class').forEach((button) => button.onclick = () => {
  state.activeClass = button.id; document.querySelectorAll('.class').forEach((item) => item.classList.toggle('active', item === button));
});
$('set-selected-class').onclick = () => { if (state.selected < 0) return; pushUndo(); state.boxes[state.selected].class_name = state.activeClass; renderCanvas(); };
$('delete').onclick = () => { if (state.selected < 0) return; pushUndo(); state.boxes.splice(state.selected, 1); state.selected = -1; renderCanvas(); };
$('undo').onclick = () => { if (!state.undo.length) return; state.redo.push(JSON.stringify(state.boxes)); state.boxes = JSON.parse(state.undo.pop()); state.selected = -1; renderCanvas(); };
$('redo').onclick = () => { if (!state.redo.length) return; state.undo.push(JSON.stringify(state.boxes)); state.boxes = JSON.parse(state.redo.pop()); state.selected = -1; renderCanvas(); };
$('copy').onclick = () => { if (!state.previous) return; pushUndo(); state.boxes = JSON.parse(JSON.stringify(state.previous)); renderCanvas(); };
$('skip').onclick = () => saveCurrent(false, 'ambiguous');
$('exclude').onclick = () => { state.boxes = []; saveCurrent(true, 'skipped'); };
$('save').onclick = () => saveCurrent(false);
$('save-next').onclick = () => saveCurrent(true);

async function saveCurrent(advance, forcedStatus = null) {
  if (!state.current) return;
  const status = forcedStatus || (state.boxes.length ? 'labelled' : 'skipped');
  await api(`/api/labels/${encodeURIComponent(state.current.name)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ boxes: state.boxes, status }) });
  state.previous = JSON.parse(JSON.stringify(state.boxes));
  const savedName = state.current.name;
  state.images = await api('/api/images').then((value) => value.images);
  const oldIndex = state.index;
  const filter = $('filter').value;
  state.filtered = state.images.filter((item) => matchesFilter(item, filter));
  const savedIndex = state.filtered.findIndex((item) => item.name === savedName);
  if (!state.filtered.length) { state.index = -1; state.current = null; renderList(); updateProgress(); renderCanvas(); return; }
  if (advance) state.index = savedIndex >= 0 ? Math.min(savedIndex + 1, state.filtered.length - 1) : Math.min(oldIndex, state.filtered.length - 1);
  else state.index = savedIndex >= 0 ? savedIndex : Math.min(oldIndex, state.filtered.length - 1);
  renderList(); updateProgress(); await loadCurrent();
}

$('prev').onclick = () => { if (state.index > 0) { state.index -= 1; loadCurrent(); } };
$('next').onclick = () => { if (state.index + 1 < state.filtered.length) { state.index += 1; loadCurrent(); } };
$('filter').onchange = applyFilter;
$('zoom').onchange = () => { state.zoom = $('zoom').value; renderCanvas(); };
window.addEventListener('resize', renderCanvas);
window.addEventListener('keydown', (event) => {
  if (event.target.tagName === 'SELECT') return;
  if (event.key === '1') $('rice').click();
  if (event.key === '2') $('flower').click();
  if (event.key === 'Delete') $('delete').click();
  if (event.key.toLowerCase() === 's') $('save').click();
});

api('/api/images').then((value) => { state.images = value.images; applyFilter(); }).catch((error) => { $('empty').textContent = error.message; });
