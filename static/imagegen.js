// Image studio — upload one input image, attach style refs, edit/save prompt,
// run through OpenAI gpt-image-1.5 or Gemini 3 Pro Image. Single global prompt
// + ref library; refs are persisted on the server, input image is per-run.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const els = {
  openaiStatus: $("#openai-status"),
  geminiStatus: $("#gemini-status"),

  inputZone: $("#input-zone"),
  inputFile: $("#input-file"),
  inputPreview: $("#input-preview"),
  inputPlaceholder: $("#input-placeholder"),
  inputClear: $("#input-clear"),

  refsGrid: $("#refs-grid"),
  refsCount: $("#refs-count"),
  refFile: $("#ref-file"),

  prompt: $("#prompt"),
  promptSave: $("#prompt-save"),
  promptReset: $("#prompt-reset"),
  promptStatus: $("#prompt-status"),

  providerBtns: $$(".provider"),
  runBtn: $("#run-btn"),
  runStatus: $("#run-status"),

  resultCard: $("#result-card"),
  resultImg: $("#result-img"),
  resultDownload: $("#result-download"),

  toast: $("#toast"),
  refTpl: $("#ref-template"),
};

const state = {
  provider: "gemini",
  inputBlob: null,
  savedPrompt: "",
  resultUrl: null,
};

let toastTimer = null;
function toast(msg, isErr = false) {
  els.toast.textContent = msg;
  els.toast.classList.toggle("err", isErr);
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, 3500);
}

// ----- Config / status -------------------------------------------------------

async function loadConfig() {
  const r = await fetch("/api/imagegen/config");
  if (!r.ok) { toast("auth required — go back and log in", true); return; }
  const c = await r.json();
  setBadge(els.openaiStatus, "OpenAI", c.openai_configured, c.openai_model);
  setBadge(els.geminiStatus, "Gemini", c.gemini_configured, c.gemini_model);
}
function setBadge(el, label, ok, sub) {
  el.textContent = `${label}: ${ok ? sub || "ready" : "missing"}`;
  el.classList.toggle("ok", !!ok);
  el.classList.toggle("bad", !ok);
}

// ----- Input image -----------------------------------------------------------

function setInput(blob) {
  state.inputBlob = blob;
  if (blob) {
    const url = URL.createObjectURL(blob);
    els.inputPreview.src = url;
    els.inputPreview.hidden = false;
    els.inputPlaceholder.hidden = true;
    els.inputClear.hidden = false;
  } else {
    els.inputPreview.src = "";
    els.inputPreview.hidden = true;
    els.inputPlaceholder.hidden = false;
    els.inputClear.hidden = true;
  }
  updateRunBtn();
}

els.inputZone.addEventListener("click", (e) => {
  if (e.target === els.inputClear) return;
  if (e.target.closest("label.link")) return;
  els.inputFile.click();
});
els.inputFile.addEventListener("change", (e) => {
  const f = e.target.files?.[0];
  if (f) setInput(f);
  e.target.value = "";
});
els.inputClear.addEventListener("click", (e) => {
  e.stopPropagation();
  setInput(null);
});
["dragenter", "dragover"].forEach((ev) =>
  els.inputZone.addEventListener(ev, (e) => {
    e.preventDefault();
    els.inputZone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  els.inputZone.addEventListener(ev, (e) => {
    e.preventDefault();
    els.inputZone.classList.remove("drag");
  })
);
els.inputZone.addEventListener("drop", (e) => {
  const f = e.dataTransfer?.files?.[0];
  if (f && f.type.startsWith("image/")) setInput(f);
});

// ----- Style refs ------------------------------------------------------------

async function loadRefs() {
  const r = await fetch("/api/imagegen/refs");
  if (!r.ok) return;
  const { refs } = await r.json();
  els.refsGrid.innerHTML = "";
  refs.forEach(({ id }) => {
    const node = els.refTpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".ref-img").src = `/api/imagegen/refs/${id}?t=${Date.now()}`;
    node.querySelector(".ref-del").addEventListener("click", () => deleteRef(id));
    els.refsGrid.appendChild(node);
  });
  els.refsCount.textContent = `${refs.length} saved`;
}

async function uploadRef(file) {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/api/imagegen/refs", { method: "POST", body: form });
  if (!r.ok) { toast("upload failed", true); return; }
}

async function deleteRef(id) {
  const r = await fetch(`/api/imagegen/refs/${id}`, { method: "DELETE" });
  if (!r.ok) { toast("delete failed", true); return; }
  await loadRefs();
}

els.refFile.addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  for (const f of files) await uploadRef(f);
  e.target.value = "";
  await loadRefs();
});

// ----- Prompt ----------------------------------------------------------------

async function loadPrompt() {
  const r = await fetch("/api/imagegen/prompt");
  if (!r.ok) return;
  const { prompt, saved } = await r.json();
  els.prompt.value = prompt;
  state.savedPrompt = prompt;
  els.promptStatus.textContent = saved
    ? "Loaded saved prompt."
    : "Using default prompt (not yet saved).";
}

function checkDirty() {
  const dirty = els.prompt.value !== state.savedPrompt;
  els.promptStatus.textContent = dirty ? "Unsaved changes." : "Saved.";
  els.promptStatus.style.color = dirty ? "#9b1c1c" : "";
}

els.prompt.addEventListener("input", checkDirty);

els.promptSave.addEventListener("click", async () => {
  const r = await fetch("/api/imagegen/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: els.prompt.value }),
  });
  if (!r.ok) { toast("save failed", true); return; }
  state.savedPrompt = els.prompt.value;
  els.promptStatus.textContent = "Saved.";
  els.promptStatus.style.color = "";
  toast("Prompt saved");
});

els.promptReset.addEventListener("click", async () => {
  if (!confirm("Reset prompt to the original default? Unsaved edits will be lost.")) return;
  const r = await fetch("/api/imagegen/prompt/reset", { method: "POST" });
  if (!r.ok) { toast("reset failed", true); return; }
  await loadPrompt();
  toast("Prompt reset");
});

// ----- Provider toggle -------------------------------------------------------

els.providerBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    els.providerBtns.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.provider = btn.dataset.provider;
  });
});

// ----- Run -------------------------------------------------------------------

function updateRunBtn() {
  els.runBtn.disabled = !state.inputBlob;
}

els.runBtn.addEventListener("click", async () => {
  if (!state.inputBlob) return;
  els.runBtn.disabled = true;
  els.runStatus.textContent = "Generating…";
  const t0 = Date.now();
  const timer = setInterval(() => {
    els.runStatus.textContent = `Generating… ${Math.floor((Date.now() - t0) / 1000)}s`;
  }, 500);

  try {
    const form = new FormData();
    form.append("file", state.inputBlob, "input.png");
    form.append("provider", state.provider);
    // Send the CURRENT textarea contents — so a user can run with an
    // unsaved edit. Persistence only happens on explicit Save.
    form.append("prompt", els.prompt.value);

    const r = await fetch("/api/imagegen/generate", {
      method: "POST",
      body: form,
    });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(text || `HTTP ${r.status}`);
    }
    const blob = await r.blob();
    if (state.resultUrl) URL.revokeObjectURL(state.resultUrl);
    state.resultUrl = URL.createObjectURL(blob);
    els.resultImg.src = state.resultUrl;
    els.resultDownload.href = state.resultUrl;
    els.resultDownload.download = `imagegen-${state.provider}-${Date.now()}.png`;
    els.resultCard.hidden = false;
    const elapsed = r.headers.get("X-Elapsed") || "";
    els.runStatus.textContent = elapsed ? `Done in ${elapsed}s` : "Done";
  } catch (e) {
    console.error(e);
    els.runStatus.textContent = "";
    toast(`Generation failed: ${String(e.message || e).slice(0, 200)}`, true);
  } finally {
    clearInterval(timer);
    els.runBtn.disabled = false;
  }
});

// ----- Boot ------------------------------------------------------------------

(async () => {
  await Promise.all([loadConfig(), loadPrompt(), loadRefs()]);
  updateRunBtn();
})();
