// Pure client-side sprite-sheet viewer. The user picks a horizontal sheet
// (frames laid out left-to-right, all square). We auto-detect the frame
// width from the image height, then animate the canvas frame-by-frame.

const file = document.getElementById('file');
const pick = document.getElementById('pick');
const dropzone = document.getElementById('dropzone');
const info = document.getElementById('info');
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const controls = document.getElementById('controls');
const fps = document.getElementById('fps');
const fpsVal = document.getElementById('fps-val');
const scale = document.getElementById('scale');
const scaleVal = document.getElementById('scale-val');

ctx.imageSmoothingEnabled = false;

let img = null;
let fW = 0;
let nFrames = 0;
let frameIdx = 0;
let lastTick = 0;
let raf = null;

function frameMs() {
  const v = parseInt(fps.value, 10);
  return v > 0 ? 1000 / v : 100;
}

function applyScale() {
  if (!img) return;
  const k = parseInt(scale.value, 10);
  scaleVal.textContent = `${k}×`;
  canvas.style.width = `${fW * k}px`;
  canvas.style.height = `${fW * k}px`;
}

function draw() {
  if (!img) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, frameIdx * fW, 0, fW, fW, 0, 0, canvas.width, canvas.height);
}

function loop(now) {
  if (now - lastTick >= frameMs()) {
    lastTick = now;
    draw();
    frameIdx = (frameIdx + 1) % nFrames;
  }
  raf = requestAnimationFrame(loop);
}

function load(blob) {
  if (raf) cancelAnimationFrame(raf);
  const url = URL.createObjectURL(blob);
  const newImg = new Image();
  newImg.onload = () => {
    if (!newImg.height || !newImg.width) {
      info.hidden = false;
      info.textContent = 'invalid image (zero dimensions)';
      return;
    }
    img = newImg;
    fW = newImg.height;
    nFrames = Math.max(1, Math.round(newImg.width / fW));
    canvas.width = fW;
    canvas.height = fW;
    frameIdx = 0;
    lastTick = 0;
    info.hidden = false;
    info.textContent = `${newImg.width}×${newImg.height}  →  ${nFrames} frames @ ${fW}×${fW}`;
    controls.hidden = false;
    applyScale();
    raf = requestAnimationFrame(loop);
  };
  newImg.onerror = () => {
    info.hidden = false;
    info.textContent = 'could not load image';
    URL.revokeObjectURL(url);
  };
  newImg.src = url;
}

pick.addEventListener('click', () => file.click());
file.addEventListener('change', (e) => {
  const f = e.target.files && e.target.files[0];
  if (f) load(f);
});

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('hover');
});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('hover'));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('hover');
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) load(f);
});

fps.addEventListener('input', () => { fpsVal.textContent = fps.value; });
scale.addEventListener('input', applyScale);
