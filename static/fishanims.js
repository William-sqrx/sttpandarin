// Fish animations gallery: rows = fish, 5 columns of animated sheets.
// All state lives on disk via /api/fishanims/* — page is read-only.

const FRAME_MS = 100;

async function loadList() {
  const r = await fetch('/api/fishanims/list', { credentials: 'same-origin' });
  if (!r.ok) {
    document.getElementById('empty').hidden = false;
    document.getElementById('empty').textContent =
      `Failed to load (${r.status}). Are you signed in?`;
    return null;
  }
  return r.json();
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

  // Animate once the sheet image loads.
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
  // Animation frames are the first (frames - 1); the last frame is a
  // duplicate of frame 0 baked in by the generator for seamless loops,
  // so playing all `frames` of them is fine.
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

function render(rows) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (const row of rows) {
    const div = document.createElement('div');
    div.className = 'row';

    const name = document.createElement('div');
    name.className = 'row-name';
    name.innerHTML = `${row.name}<small>${row.sheets.length} sheet${row.sheets.length !== 1 ? 's' : ''}</small>`;
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

(async () => {
  const data = await loadList();
  if (!data) return;
  document.getElementById('count').textContent = `${data.count} fish`;
  if (data.count === 0) {
    document.getElementById('empty').hidden = false;
    return;
  }
  render(data.rows);
})();
