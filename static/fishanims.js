// Fish animations gallery: rows = fish, 5 columns of animated sheets.
// Polls /api/fishanims/list + /api/fishanims/batch/status every 5s while
// the batch is running, so progress is live and the prod dyno stays warm.

const FRAME_MS = 100;
const POLL_MS = 5000;

// ----- Sandbox: upload-and-animate any sprite sheet -------------------------
// Self-contained. Supports both a horizontal strip (cols×1) and a full grid
// (cols×rows) — frames are read left-to-right, top-to-bottom. The `frames`
// field trims trailing empty cells (e.g. a 5×5 sheet with the bottom-right
// cell blank → cols 5, rows 5, frames 24).
(function initSandbox() {
  const drop = document.getElementById('sandbox-drop');
  const fileInput = document.getElementById('sandbox-file');
  const canvas = document.getElementById('sandbox-canvas');
  const meta = document.getElementById('sandbox-meta');
  const fpsRange = document.getElementById('sandbox-fps');
  const fpsLabel = document.getElementById('sandbox-fps-val');
  const toggleBtn = document.getElementById('sandbox-toggle');
  const clearBtn = document.getElementById('sandbox-clear');
  const colsInput = document.getElementById('sandbox-cols');
  const rowsInput = document.getElementById('sandbox-rows');
  const framesInput = document.getElementById('sandbox-frames');
  if (!drop || !canvas) return;

  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;

  let img = null;
  let objectUrl = null;
  let fileName = '';
  let cols = 1;
  let rows = 1;
  let frames = 1;
  let frameW = 256;
  let frameH = 256;
  let playing = false;
  let frameIdx = 0;
  let lastTick = 0;
  let rafId = 0;

  const clampInt = (v, lo, hi, dflt) => {
    const n = parseInt(v, 10);
    if (!Number.isFinite(n)) return dflt;
    return Math.min(hi, Math.max(lo, n));
  };

  function drawIdle(text) {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (text) {
      ctx.fillStyle = '#aab2c0';
      ctx.font = '12px ui-sans-serif, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, canvas.width / 2, canvas.height / 2);
    }
  }

  function drawFrame() {
    if (!img) return;
    const col = frameIdx % cols;
    const row = Math.floor(frameIdx / cols);
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(
      img,
      col * frameW, row * frameH,
      frameW, frameH,
      0, 0,
      canvas.width, canvas.height,
    );
  }

  // Recompute frame geometry from the cols/rows/frames inputs + current image.
  // Called on load and whenever the user edits a grid field. Normalises the
  // input values (clamps, caps frames at cols*rows) and reflects them back.
  function reframe() {
    if (!img) return;
    cols = clampInt(colsInput.value, 1, 40, 1);
    rows = clampInt(rowsInput.value, 1, 40, 1);
    const maxFrames = cols * rows;
    frames = clampInt(framesInput.value, 1, maxFrames, maxFrames);
    frameW = Math.round(img.naturalWidth / cols);
    frameH = Math.round(img.naturalHeight / rows);

    colsInput.value = cols;
    rowsInput.value = rows;
    framesInput.value = frames;
    framesInput.max = maxFrames;

    // Canvas buffer = one frame so drawImage scales 1:1; CSS aspect-ratio
    // follows the frame so non-square frames aren't squished.
    canvas.width = frameW;
    canvas.height = frameH;
    canvas.style.aspectRatio = `${frameW} / ${frameH}`;
    frameIdx = 0;
    meta.textContent =
      `${fileName} · ${img.naturalWidth}×${img.naturalHeight} · `
      + `${cols}×${rows} grid · `
      + `${frames} frame${frames === 1 ? '' : 's'} of ${frameW}×${frameH}`;
    drawFrame();
  }

  function loop(now) {
    if (!playing || !img) return;
    const fps = Math.max(1, parseInt(fpsRange.value, 10) || 10);
    const stepMs = 1000 / fps;
    if (now - lastTick >= stepMs) {
      drawFrame();
      frameIdx = (frameIdx + 1) % Math.max(1, frames);
      lastTick = now;
    }
    rafId = requestAnimationFrame(loop);
  }

  function startLoop() {
    if (!img) return;
    playing = true;
    toggleBtn.textContent = '⏸ pause';
    cancelAnimationFrame(rafId);
    lastTick = performance.now();
    rafId = requestAnimationFrame(loop);
  }

  function pauseLoop() {
    playing = false;
    toggleBtn.textContent = '▶ play';
    cancelAnimationFrame(rafId);
  }

  function clearSheet() {
    pauseLoop();
    img = null;
    fileName = '';
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    cols = 1;
    rows = 1;
    frames = 1;
    frameIdx = 0;
    colsInput.value = 1;
    rowsInput.value = 1;
    framesInput.value = 1;
    canvas.width = 256;
    canvas.height = 256;
    canvas.style.aspectRatio = '1 / 1';
    drawIdle('no sheet loaded');
    meta.textContent = 'no sheet loaded';
    toggleBtn.disabled = true;
    clearBtn.disabled = true;
    fileInput.value = '';
  }

  function loadFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      meta.textContent = `unsupported file type: ${file.type || 'unknown'}`;
      return;
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    const next = new Image();
    next.onload = () => {
      img = next;
      fileName = file.name;
      // First guess: a horizontal strip of square frames (the convention
      // used elsewhere in the app). Grid sheets won't match — the user
      // sets cols/rows by hand and reframe() picks it up.
      const guessCols = Math.max(
        1, Math.round(next.naturalWidth / next.naturalHeight),
      );
      colsInput.value = guessCols;
      rowsInput.value = 1;
      framesInput.value = guessCols;
      reframe();
      toggleBtn.disabled = false;
      clearBtn.disabled = false;
      startLoop();
    };
    next.onerror = () => {
      meta.textContent = `failed to decode "${file.name}" — is it a valid image?`;
    };
    next.src = objectUrl;
  }

  fileInput.addEventListener('change', () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) loadFile(f);
  });

  ['dragenter', 'dragover'].forEach((ev) => {
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add('drag');
    });
  });
  ['dragleave', 'drop'].forEach((ev) => {
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      if (ev === 'dragleave' && e.target !== drop) return;
      drop.classList.remove('drag');
    });
  });
  drop.addEventListener('drop', (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) loadFile(f);
  });

  fpsRange.addEventListener('input', () => {
    fpsLabel.textContent = fpsRange.value;
  });

  // Re-slice the sheet whenever a grid field changes. Animation keeps
  // running — reframe() resets frameIdx so playback restarts cleanly.
  [colsInput, rowsInput, framesInput].forEach((el) => {
    el.addEventListener('change', reframe);
  });

  toggleBtn.addEventListener('click', () => {
    if (playing) pauseLoop();
    else startLoop();
  });
  clearBtn.addEventListener('click', clearSheet);

  drawIdle('no sheet loaded');
})();

const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const controlHint = document.getElementById('control-hint');

async function startBatch() {
  startBtn.disabled = true;
  const original = startBtn.textContent;
  startBtn.textContent = 'starting…';
  try {
    const r = await fetch('/api/fishanims/batch/start', {
      method: 'POST',
      credentials: 'same-origin',
    });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(`${r.status} ${text.slice(0, 120)}`);
    }
    // Refresh state immediately so the user sees the running state.
    setTimeout(tick, 200);
  } catch (e) {
    alert(`start failed: ${e.message}`);
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = original;
  }
}

async function stopBatch() {
  if (!confirm('Stop the batch?\n(Already-saved sheets stay in MongoDB. You can resume any time by clicking Start.)')) {
    return;
  }
  stopBtn.disabled = true;
  const original = stopBtn.textContent;
  stopBtn.textContent = 'stopping…';
  try {
    const r = await fetch('/api/fishanims/batch/stop', {
      method: 'POST',
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error(`${r.status}`);
    setTimeout(tick, 200);
  } catch (e) {
    alert(`stop failed: ${e.message}`);
  } finally {
    stopBtn.disabled = false;
    stopBtn.textContent = original;
  }
}

startBtn.addEventListener('click', startBatch);
stopBtn.addEventListener('click', stopBatch);

let lastListKey = "";  // hash of (name + idx-list) so we re-render only on change
let skippedFish = new Set();
let regenQueue = new Set();
let customRefs = new Set();  // species names with a user-uploaded ref image
let batchRunning = false;
const rowEls = new Map();  // species name → row DOM element (so we can update
                            // .generating class without re-rendering the grid)
let lastCurrentStem = null;
const revealedRows = new Set();  // names whose canvases are currently animating

async function fetchJSON(url) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

function listKey(rows) {
  // Include hasCustomRef in the hash so toggling a custom ref re-renders
  // the row with the right badge + reset button.
  return rows.map(r =>
    r.name + ':' + r.sheets.map(s => s.idx).join(',') + ':' + (r.hasCustomRef ? '1' : '0'),
  ).join('|');
}

function augmentRows(rows, status) {
  // The /api/fishanims/list endpoint only returns fish that have at least
  // 1 sheet in MongoDB. Augment with placeholder rows for the currently-
  // generating fish (so the user can see "this is what's happening RIGHT
  // NOW") and any persistently-skipped fish (so they can un-skip them).
  const byName = new Map(rows.map(r => [r.name, r]));

  if (status && status.state === 'running' && status.current) {
    const stem = status.current.split(' ')[0];
    if (stem && !byName.has(stem)) {
      byName.set(stem, { name: stem, sheets: [] });
    }
  }

  const skips = (status && status.skipped_fish) || [];
  for (const stem of skips) {
    if (!byName.has(stem)) {
      byName.set(stem, { name: stem, sheets: [] });
    }
  }

  return Array.from(byName.values()).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
}

async function loadList() {
  try {
    return await fetchJSON('/api/fishanims/list');
  } catch (e) {
    document.getElementById('empty').hidden = false;
    document.getElementById('empty').textContent =
      `Failed to load (${e.message}). Are you signed in?`;
    return null;
  }
}

async function loadStatus() {
  try {
    return await fetchJSON('/api/fishanims/batch/status');
  } catch {
    return null;
  }
}

function makeCell(name, sheet) {
  const cell = document.createElement('div');
  cell.className = 'cell';

  const canvas = document.createElement('canvas');
  canvas.width = sheet.frameW;
  canvas.height = sheet.frameH;
  cell.appendChild(canvas);

  const actions = document.createElement('div');
  actions.className = 'actions';
  const idxLabel = document.createElement('span');
  idxLabel.className = 'idx';
  idxLabel.textContent = `#${sheet.idx}`;
  const dl = document.createElement('a');
  dl.className = 'dl-btn';
  dl.href = `/api/fishanims/${encodeURIComponent(name)}/${sheet.idx}/download`;
  dl.textContent = 'Download';
  dl.setAttribute('download', `${name}_${sheet.idx}.png`);
  actions.appendChild(idxLabel);
  actions.appendChild(dl);
  cell.appendChild(actions);

  // Lazy-load: don't fetch the sprite sheet on row creation. With many
  // rows the parallel image fetches choked the page. Instead the URL
  // is stashed on the canvas and the actual <Image> is only created
  // when the row's Reveal button is pressed (loadSheet below). Hidden
  // rows render a flat placeholder so the layout still reserves space.
  canvas._sheet = sheet;
  canvas._url = `/api/fishanims/${encodeURIComponent(name)}/${sheet.idx}/sheet`;
  canvas._name = name;
  drawPlaceholder(canvas);
  // If a previous reveal toggle for this fish persists across re-renders,
  // honour it on remount.
  if (revealedRows.has(name)) {
    loadSheet(canvas);
  }

  return cell;
}

// Flat dark placeholder — drawn for every cell on creation and after a
// hide so the canvas isn't blank-white in the gallery grid.
function drawPlaceholder(canvas) {
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

// Lazy-load the sprite sheet for a single canvas. Caches the loaded
// Image on the canvas so repeated reveal toggles after the first don't
// re-fetch. Once loaded, picks up whatever the row's reveal state is
// at that moment.
function loadSheet(canvas) {
  if (canvas._img) {
    // Already loaded — just react to current reveal state.
    if (revealedRows.has(canvas._name)) {
      startAnimate(canvas, canvas._img, canvas._sheet);
    } else {
      drawFrame0(canvas, canvas._img, canvas._sheet);
    }
    return;
  }
  if (canvas._loading) return;
  canvas._loading = true;
  const img = new Image();
  img.src = canvas._url;
  img.onload = () => {
    canvas._img = img;
    canvas._loading = false;
    if (revealedRows.has(canvas._name)) {
      startAnimate(canvas, img, canvas._sheet);
    } else {
      drawFrame0(canvas, img, canvas._sheet);
    }
  };
  img.onerror = () => {
    canvas._loading = false;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#3a2030';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  };
}

function drawFrame0(canvas, img, sheet) {
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(
    img,
    0, 0, sheet.frameW, sheet.frameH,
    0, 0, canvas.width, canvas.height,
  );
}

function startAnimate(canvas, img, sheet) {
  if (canvas._animating) return;
  canvas._animating = true;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const total = sheet.frames || 1;
  let i = 0;
  let last = performance.now();
  const tick = (now) => {
    if (!canvas.isConnected || !canvas._animating) return;
    if (now - last >= FRAME_MS) {
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(
        img,
        i * sheet.frameW, 0,
        sheet.frameW, sheet.frameH,
        0, 0,
        canvas.width, canvas.height,
      );
      i = (i + 1) % total;
      last = now;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function stopAnimate(canvas) {
  canvas._animating = false;
  // Hidden state — fall back to the flat placeholder so no fish image
  // is visible until the user explicitly re-reveals.
  drawPlaceholder(canvas);
}

function toggleReveal(name, rowEl) {
  const wasRevealed = revealedRows.has(name);
  if (wasRevealed) {
    revealedRows.delete(name);
    for (const c of rowEl.querySelectorAll('canvas')) stopAnimate(c);
  } else {
    revealedRows.add(name);
    for (const c of rowEl.querySelectorAll('canvas')) {
      // First reveal triggers the lazy fetch + animate; subsequent
      // reveals reuse the cached Image so the page doesn't re-load.
      loadSheet(c);
    }
  }
  const btn = rowEl.querySelector('.reveal-btn');
  if (btn) {
    btn.textContent = wasRevealed ? '▶ reveal' : '⏸ hide';
    btn.classList.toggle('revealed', !wasRevealed);
  }
}

async function skipFish(name, button) {
  button.disabled = true;
  button.textContent = '…';
  try {
    const r = await fetch(`/api/fishanims/batch/skip/${encodeURIComponent(name)}`, {
      method: 'POST',
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error(`${r.status}`);
    skippedFish.add(name);
    // Re-render to swap the button into its unskip state.
    lastListKey = '';
  } catch (e) {
    button.disabled = false;
    button.textContent = 'Skip rest →';
    alert(`skip failed: ${e.message}`);
  }
  tick();
}

async function unskipFish(name, button) {
  button.disabled = true;
  button.textContent = '…';
  try {
    const r = await fetch(`/api/fishanims/batch/unskip/${encodeURIComponent(name)}`, {
      method: 'POST',
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error(`${r.status}`);
    skippedFish.delete(name);
    lastListKey = '';
  } catch (e) {
    button.disabled = false;
    button.textContent = '✗ unskip';
    alert(`unskip failed: ${e.message}`);
  }
  tick();
}

async function regenFish(name, button) {
  if (!confirm(`Regenerate all 5 sheets for ${name}?\n(Existing sheets will be wiped — costs 5 fresh PixelLab calls.)`)) {
    return;
  }
  button.disabled = true;
  button.textContent = '…';
  try {
    // If the batch isn't running, kick it off so the worker drains the regen
    // queue. Start must happen FIRST — the start handler clears _regen_queue,
    // so we enqueue after.
    if (!batchRunning) {
      const sr = await fetch('/api/fishanims/batch/start', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (!sr.ok) {
        const text = await sr.text();
        throw new Error(`start failed: ${sr.status} ${text.slice(0, 120)}`);
      }
    }
    const r = await fetch(`/api/fishanims/batch/regen/${encodeURIComponent(name)}`, {
      method: 'POST',
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error(`${r.status}`);
    regenQueue.add(name);
    button.textContent = 'queued';
    button.classList.add('queued');
    setTimeout(tick, 200);
  } catch (e) {
    button.disabled = false;
    button.textContent = 'Regen ↻';
    alert(`regen failed: ${e.message}`);
  }
}

// Replace the reference image used to generate a fish's animations. Goes
// straight to MongoDB so the upload survives Render dyno restarts. The
// next Regen for this fish picks up the new reference automatically.
async function uploadRef(name, file, button, refImg) {
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) {
    alert(`File too large (${(file.size / 1024 / 1024).toFixed(1)}MB). 8MB max.`);
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = 'uploading…';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(`/api/fishanims/${encodeURIComponent(name)}/ref`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(`${r.status} ${text.slice(0, 160)}`);
    }
    // Force the row's ref preview to refresh (cache-bust query param).
    if (refImg) {
      refImg.src = `/api/fishanims/${encodeURIComponent(name)}/ref?t=${Date.now()}`;
    }
    // Flag so the row re-renders with the "Custom" badge + reset btn.
    customRefs.add(name);
    lastListKey = '';
    tick();
  } catch (e) {
    alert(`upload failed: ${e.message}`);
    button.disabled = false;
    button.textContent = original;
  }
}

async function resetRef(name, button) {
  if (!confirm(`Reset ${name} back to the default reference image?\n(Your uploaded image will be deleted. Regen to apply.)`)) {
    return;
  }
  button.disabled = true;
  button.textContent = '…';
  try {
    const r = await fetch(`/api/fishanims/${encodeURIComponent(name)}/ref`, {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error(`${r.status}`);
    customRefs.delete(name);
    lastListKey = '';
    tick();
  } catch (e) {
    alert(`reset failed: ${e.message}`);
    button.disabled = false;
    button.textContent = '↺ default';
  }
}

function renderGrid(rows) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  rowEls.clear();
  for (const row of rows) {
    const div = document.createElement('div');
    div.className = 'row';
    rowEls.set(row.name, div);

    const isComplete = row.sheets.length >= 5;
    const isSkipped = skippedFish.has(row.name);
    const isQueued = regenQueue.has(row.name);
    const hasCustomRef = !!row.hasCustomRef || customRefs.has(row.name);

    const name = document.createElement('div');
    name.className = 'row-name';

    // Reference image preview — clicking it opens the file picker so the
    // thumbnail itself is the upload affordance, not just the small button.
    const refWrap = document.createElement('div');
    refWrap.className = 'ref-wrap';
    refWrap.title = 'Click to upload a new reference image';

    const refImg = document.createElement('img');
    refImg.className = 'ref-img';
    refImg.alt = `${row.name} reference`;
    refImg.loading = 'lazy';
    refImg.src = `/api/fishanims/${encodeURIComponent(row.name)}/ref`;
    refImg.onerror = () => {
      // No on-disk default AND no upload yet — show a placeholder tile
      // so the row layout doesn't collapse.
      refImg.style.display = 'none';
      refWrap.classList.add('ref-empty');
      refWrap.textContent = 'no ref';
    };
    refWrap.appendChild(refImg);

    if (hasCustomRef) {
      const badge = document.createElement('span');
      badge.className = 'ref-badge';
      badge.textContent = 'CUSTOM';
      refWrap.appendChild(badge);
    }

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/png,image/jpeg,image/webp';
    fileInput.style.display = 'none';
    refWrap.appendChild(fileInput);

    refWrap.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      if (file) uploadRef(row.name, file, uploadBtn, refImg);
      fileInput.value = '';  // allow re-selecting the same file
    });

    name.appendChild(refWrap);

    const nameText = document.createElement('div');
    nameText.className = 'row-name-text';
    nameText.innerHTML = `${row.name}<small>${row.sheets.length}/5 sheets</small>`;
    name.appendChild(nameText);

    const genPill = document.createElement('div');
    genPill.className = 'gen-pill';
    genPill.hidden = true;
    name.appendChild(genPill);

    const btnRow = document.createElement('div');
    btnRow.className = 'btn-row';

    // Upload button — separate from the thumbnail so keyboard / screen-
    // reader users have an explicit control. Always enabled.
    const uploadBtn = document.createElement('button');
    uploadBtn.type = 'button';
    uploadBtn.className = 'upload-btn';
    uploadBtn.textContent = hasCustomRef ? '↑ replace' : '↑ upload';
    uploadBtn.title = 'Upload a new reference image — used the next time you Regen this fish';
    uploadBtn.addEventListener('click', () => fileInput.click());
    btnRow.appendChild(uploadBtn);

    // Reset button — only shown when a custom ref is currently in place.
    if (hasCustomRef) {
      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.className = 'reset-btn';
      resetBtn.textContent = '↺ default';
      resetBtn.title = 'Drop the uploaded ref so the next Regen uses the on-disk default';
      resetBtn.addEventListener('click', () => resetRef(row.name, resetBtn));
      btnRow.appendChild(resetBtn);
    }

    // Skip button — clicking when already skipped reverses it (un-skip).
    const skipBtn = document.createElement('button');
    skipBtn.type = 'button';
    skipBtn.className = 'skip-btn';
    if (isSkipped) {
      skipBtn.textContent = '✗ unskip';
      skipBtn.classList.add('skipped');
      skipBtn.title = 'Reverse the skip — fish becomes eligible for generation again';
      skipBtn.addEventListener('click', () => unskipFish(row.name, skipBtn));
    } else if (isComplete) {
      skipBtn.textContent = 'done';
      skipBtn.disabled = true;
    } else if (!batchRunning) {
      skipBtn.textContent = 'idle';
      skipBtn.disabled = true;
    } else {
      skipBtn.textContent = 'Skip rest →';
      skipBtn.addEventListener('click', () => skipFish(row.name, skipBtn));
    }
    btnRow.appendChild(skipBtn);

    // Reveal button — toggles per-row animation. Default is static (frame 0)
    // so the page stays smooth even with 30+ rows.
    const revealBtn = document.createElement('button');
    revealBtn.type = 'button';
    revealBtn.className = 'reveal-btn';
    if (row.sheets.length === 0) {
      revealBtn.textContent = '—';
      revealBtn.disabled = true;
    } else {
      const isRevealed = revealedRows.has(row.name);
      revealBtn.textContent = isRevealed ? '⏸ hide' : '▶ reveal';
      if (isRevealed) revealBtn.classList.add('revealed');
      revealBtn.addEventListener('click', () => toggleReveal(row.name, div));
    }
    btnRow.appendChild(revealBtn);

    // Regen button — enabled for any fish with at least 1 sheet. If the
    // batch isn't running when clicked, regenFish auto-starts it so the
    // worker can drain the regen queue.
    const regenBtn = document.createElement('button');
    regenBtn.type = 'button';
    regenBtn.className = 'regen-btn';
    if (isQueued) {
      regenBtn.textContent = 'queued';
      regenBtn.classList.add('queued');
      regenBtn.disabled = true;
    } else if (row.sheets.length === 0) {
      regenBtn.textContent = '↻';
      regenBtn.disabled = true;
    } else {
      regenBtn.textContent = 'Regen ↻';
      regenBtn.addEventListener('click', () => regenFish(row.name, regenBtn));
    }
    btnRow.appendChild(regenBtn);

    name.appendChild(btnRow);
    div.appendChild(name);

    for (let i = 0; i < 5; i++) {
      const sheet = row.sheets[i];
      if (sheet) {
        div.appendChild(makeCell(row.name, sheet));
      } else {
        const blank = document.createElement('div');
        blank.className = 'cell';
        blank.style.opacity = '0.35';
        blank.innerHTML = '<canvas width="256" height="256"></canvas><div class="actions"><span class="idx">—</span></div>';
        div.appendChild(blank);
      }
    }

    grid.appendChild(div);
  }
}

function updateGeneratingHighlight(status) {
  const cur = (status && status.current) || '';
  // _status.current is "<stem> <idx>/<total>" (or with trailing " (regen)")
  const stem = cur.split(' ')[0] || null;
  const detail = stem ? cur.slice(stem.length).trim() : '';

  for (const [name, el] of rowEls) {
    const pill = el.querySelector('.gen-pill');
    if (name === stem && batchRunning) {
      el.classList.add('generating');
      pill.textContent = `⟳ ${detail}`;
      pill.hidden = false;
    } else {
      el.classList.remove('generating');
      if (pill) pill.hidden = true;
    }
  }

  // Auto-scroll to the row that just became active.
  if (stem && stem !== lastCurrentStem && batchRunning) {
    const el = rowEls.get(stem);
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    lastCurrentStem = stem;
  }
}

function renderStatus(s) {
  if (!s) return;
  const bar = document.getElementById('status-bar');
  const state = document.getElementById('status-state');
  const prog = document.getElementById('status-progress');
  const cur = document.getElementById('status-current');
  const errBanner = document.getElementById('error-banner');

  if (s.state === 'idle') {
    bar.hidden = true;
  } else {
    bar.hidden = false;
    state.textContent = s.state;
    state.dataset.state = s.state;

    const totalSeen = s.done + s.skipped + s.failed;
    const tot = s.total || totalSeen;
    prog.textContent = tot > 0
      ? `${totalSeen}/${tot}  (done ${s.done} · skip ${s.skipped} · fail ${s.failed})`
      : '';
    cur.textContent = s.current ? `→ ${s.current}` : '';
  }

  // Surface errors prominently. Fatal (state=error) takes precedence;
  // otherwise show the most recent per-sheet failure if there are fails.
  let errText = '';
  if (s.state === 'error' && s.error) {
    errText = `⚠ ${s.error}`;
  } else if (s.failed > 0 && s.last_error) {
    errText = `⚠ last failure — ${s.last_error}`;
  }
  if (errText) {
    errBanner.textContent = errText;
    errBanner.hidden = false;
    errBanner.classList.toggle('fatal', s.state === 'error');
  } else {
    errBanner.hidden = true;
  }
}

async function tick() {
  const [list, status] = await Promise.all([loadList(), loadStatus()]);
  renderStatus(status);

  // Keep skip-state in sync with the server (browser refreshes pick up
  // already-skipped fish; running state controls whether button is active).
  const wasRunning = batchRunning;
  batchRunning = !!(status && status.state === 'running');

  // Toggle Start/Stop visibility based on batch state.
  startBtn.hidden = batchRunning;
  stopBtn.hidden = !batchRunning;
  if (status) {
    if (batchRunning) {
      controlHint.textContent = '';
    } else if (status.state === 'finished') {
      controlHint.textContent = 'all done — click Start to add more / regenerate';
    } else if (status.state === 'stopped') {
      controlHint.textContent = 'stopped — click Start to resume';
    } else if (status.state === 'error') {
      controlHint.textContent = `error: ${status.error || 'unknown'} — click Start to retry`;
    } else {
      controlHint.textContent = 'idle — click Start to begin';
    }
  }
  const newSkipped = new Set((status && status.skipped_fish) || []);
  const newRegen = new Set((status && status.regen_queue) || []);
  const skippedChanged = newSkipped.size !== skippedFish.size
    || [...newSkipped].some(n => !skippedFish.has(n));
  const regenChanged = newRegen.size !== regenQueue.size
    || [...newRegen].some(n => !regenQueue.has(n));
  skippedFish = newSkipped;
  regenQueue = newRegen;

  if (list) {
    document.getElementById('count').textContent = `${list.count} fish`;
    // Resync the customRefs cache from the server payload so a refresh
    // (or another browser tab uploading) picks up the badge state.
    customRefs = new Set(
      (list.rows || []).filter(r => r.hasCustomRef).map(r => r.name),
    );
    // Use augmented rows so the currently-generating fish + skipped fish
    // appear in the grid even when they have 0 sheets yet.
    const augmented = augmentRows(list.rows, status);
    if (augmented.length === 0 && (!status || status.state === 'idle')) {
      document.getElementById('empty').hidden = false;
    } else {
      document.getElementById('empty').hidden = true;
    }
    const key = listKey(augmented);
    // Re-render if list shape changed OR running-state flipped OR skip/regen set changed
    if (key !== lastListKey || wasRunning !== batchRunning || skippedChanged || regenChanged) {
      lastListKey = key;
      renderGrid(augmented);
    }
  }

  // Update the currently-generating row's highlight + auto-scroll. This
  // runs every tick so the visual updates without re-rendering the grid
  // (which would restart canvas animations).
  updateGeneratingHighlight(status);

  const fast = batchRunning;
  setTimeout(tick, fast ? POLL_MS : POLL_MS * 4);
}

tick();
