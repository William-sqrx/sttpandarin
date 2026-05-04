// Fish animations gallery: rows = fish, 5 columns of animated sheets.
// Polls /api/fishanims/list + /api/fishanims/batch/status every 5s while
// the batch is running, so progress is live and the prod dyno stays warm.

const FRAME_MS = 100;
const POLL_MS = 5000;

let lastListKey = "";  // hash of (name + idx-list) so we re-render only on change
let skippedFish = new Set();
let batchRunning = false;

async function fetchJSON(url) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

function listKey(rows) {
  return rows.map(r => r.name + ':' + r.sheets.map(s => s.idx).join(',')).join('|');
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

  const img = new Image();
  img.src = `/api/fishanims/${encodeURIComponent(name)}/${sheet.idx}/sheet`;
  img.onload = () => animate(canvas, img, sheet);
  img.onerror = () => {
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#3a2030';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  };

  return cell;
}

function animate(canvas, img, sheet) {
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const total = sheet.frames || 1;
  let i = 0;
  let last = performance.now();
  const tick = (now) => {
    if (!canvas.isConnected) return;
    if (now - last >= FRAME_MS) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
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
    button.textContent = 'skipped';
    button.classList.add('skipped');
  } catch (e) {
    button.disabled = false;
    button.textContent = 'Skip rest →';
    alert(`skip failed: ${e.message}`);
  }
}

function renderGrid(rows) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (const row of rows) {
    const div = document.createElement('div');
    div.className = 'row';

    const isComplete = row.sheets.length >= 5;
    const isSkipped = skippedFish.has(row.name);

    const name = document.createElement('div');
    name.className = 'row-name';
    const skipBtn = document.createElement('button');
    skipBtn.type = 'button';
    skipBtn.className = 'skip-btn';
    if (isSkipped) {
      skipBtn.textContent = 'skipped';
      skipBtn.classList.add('skipped');
      skipBtn.disabled = true;
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
    name.innerHTML = `<div class="row-name-text">${row.name}<small>${row.sheets.length}/5 sheets</small></div>`;
    name.appendChild(skipBtn);
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

function renderStatus(s) {
  if (!s) return;
  const bar = document.getElementById('status-bar');
  const state = document.getElementById('status-state');
  const prog = document.getElementById('status-progress');
  const cur = document.getElementById('status-current');

  if (s.state === 'idle') {
    bar.hidden = true;
    return;
  }
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

async function tick() {
  const [list, status] = await Promise.all([loadList(), loadStatus()]);
  renderStatus(status);

  // Keep skip-state in sync with the server (browser refreshes pick up
  // already-skipped fish; running state controls whether button is active).
  const wasRunning = batchRunning;
  batchRunning = !!(status && status.state === 'running');
  const newSkipped = new Set((status && status.skipped_fish) || []);
  const skippedChanged = newSkipped.size !== skippedFish.size
    || [...newSkipped].some(n => !skippedFish.has(n));
  skippedFish = newSkipped;

  if (list) {
    document.getElementById('count').textContent = `${list.count} fish`;
    if (list.count === 0 && (!status || status.state === 'idle')) {
      document.getElementById('empty').hidden = false;
    } else {
      document.getElementById('empty').hidden = true;
    }
    const key = listKey(list.rows);
    // Re-render if list shape changed OR running-state flipped OR skip-set changed
    if (key !== lastListKey || wasRunning !== batchRunning || skippedChanged) {
      lastListKey = key;
      renderGrid(list.rows);
    }
  }

  const fast = batchRunning;
  setTimeout(tick, fast ? POLL_MS : POLL_MS * 4);
}

tick();
