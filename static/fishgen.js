/* Fish generator UI — fetches /api/fishgen/list, builds the grid,
   and wires per-cell generate / animate / prompt-edit actions. */

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    species: [],
    stages: [],
    openaiOk: false,
    pixellabOk: false,
  };

  // ─── HTTP helpers ────────────────────────────────────────────────────

  async function api(method, path, body) {
    const opts = { method, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    if (r.status === 401) {
      window.location.href = "/";
      throw new Error("not authenticated");
    }
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    if (r.status === 204) return null;
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("application/json")) return await r.json();
    return await r.blob();
  }

  function toast(msg, kind = "") {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 3500);
  }

  // ─── Sprite-sheet swim animation ─────────────────────────────────────

  function startSheetAnimation(canvas, blobUrl) {
    stopSheetAnimation(canvas);
    const img = new Image();
    img.onload = () => {
      const fW = img.height;            // frames are square
      if (!fW) return;
      const nFrames = Math.max(1, Math.round(img.width / fW));
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const cssSize = canvas.getBoundingClientRect().width || 200;
      canvas.width = Math.round(cssSize * dpr);
      canvas.height = Math.round(cssSize * dpr);
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = false;
      let idx = 0;
      let last = 0;
      const FPS = 10;
      function tick(ts) {
        if (!canvas.isConnected) return;        // bail if removed
        if (ts - last >= 1000 / FPS) {
          last = ts;
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(
            img,
            idx * fW, 0, fW, fW,
            0, 0, canvas.width, canvas.height,
          );
          idx = (idx + 1) % nFrames;
        }
        canvas._raf = requestAnimationFrame(tick);
      }
      canvas._raf = requestAnimationFrame(tick);
    };
    img.onerror = () => { stopSheetAnimation(canvas); };
    img.src = blobUrl;
    canvas._img = img;
    canvas._blobUrl = blobUrl;
  }

  function stopSheetAnimation(canvas) {
    if (canvas._raf) cancelAnimationFrame(canvas._raf);
    canvas._raf = null;
    if (canvas._blobUrl) {
      try { URL.revokeObjectURL(canvas._blobUrl); } catch {}
      canvas._blobUrl = null;
    }
  }

  async function refreshSheet(cell, slug, stage) {
    const canvas = $(".sheet", cell);
    try {
      const r = await fetch(`/api/fishgen/${slug}/${stage}/sheet?t=${Date.now()}`,
                            { credentials: "same-origin" });
      if (!r.ok) {
        canvas.hidden = true;
        return false;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      canvas.hidden = false;
      $(".still", cell).hidden = true;
      $(".placeholder", cell).hidden = true;
      startSheetAnimation(canvas, url);
      return true;
    } catch {
      canvas.hidden = true;
      return false;
    }
  }

  // ─── Cell rendering ──────────────────────────────────────────────────

  function setBusy(cell, msg) {
    const overlay = $(".overlay", cell);
    overlay.hidden = !msg;
    if (msg) $(".overlay-text", overlay).textContent = msg;
    $$("button", cell).forEach(b => { b.disabled = !!msg; });
  }

  function setStatus(cell, msg, kind = "") {
    const el = $(".status", cell);
    el.textContent = msg || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  async function reloadStill(cell, slug, stage) {
    const img = $(".still", cell);
    const canvas = $(".sheet", cell);
    const placeholder = $(".placeholder", cell);
    stopSheetAnimation(canvas);
    canvas.hidden = true;
    try {
      const r = await fetch(`/api/fishgen/${slug}/${stage}/image?t=${Date.now()}`,
                            { credentials: "same-origin" });
      if (!r.ok) {
        img.hidden = true;
        img.removeAttribute("src");
        placeholder.hidden = false;
        return false;
      }
      const blob = await r.blob();
      const prevUrl = img._blobUrl;
      const url = URL.createObjectURL(blob);
      img.hidden = false;
      placeholder.hidden = true;
      img.src = url;
      img._blobUrl = url;
      if (prevUrl) { try { URL.revokeObjectURL(prevUrl); } catch {} }
      return true;
    } catch {
      return false;
    }
  }

  function applyMeta(cell, meta) {
    cell.dataset.hasImage = meta.has_image ? "1" : "";
    cell.dataset.hasSheet = meta.has_sheet ? "1" : "";
    $(".animate", cell).disabled = !meta.has_image || !state.pixellabOk;
    $(".wipe", cell).hidden = !meta.has_image && !meta.has_sheet;
    $(".prompt", cell).value = meta.prompt || "";
  }

  async function loadCell(cell) {
    const { slug, stage } = cell.dataset;
    const meta = state.metas[`${slug}/${stage}`];
    applyMeta(cell, meta);
    if (meta.has_image) await reloadStill(cell, slug, stage);
    if (meta.has_sheet) await refreshSheet(cell, slug, stage);
  }

  // Also keep a flat lookup for incremental updates.
  state.metas = {};

  function rebuildMetas() {
    state.metas = {};
    for (const sp of state.species) {
      for (const m of sp.stages) {
        state.metas[`${m.slug}/${m.stage}`] = m;
      }
    }
  }

  // ─── Action handlers ─────────────────────────────────────────────────

  async function onGenerate(cell) {
    const { slug, stage } = cell.dataset;
    const promptEl = $(".prompt", cell);
    let prompt = promptEl.value.trim();
    if (!prompt) {
      // Pull a fresh default if user cleared it.
      try {
        const r = await api("GET",
          `/api/fishgen/${slug}/${stage}/prompt`);
        prompt = r.prompt;
        promptEl.value = prompt;
      } catch {}
    }
    if (!prompt) {
      toast("Prompt is empty", "err");
      return;
    }
    setBusy(cell, "generating…");
    setStatus(cell, "calling OpenAI (10–30 s)…", "busy");
    try {
      const out = await api("POST",
        `/api/fishgen/${slug}/${stage}/generate`,
        { prompt, save_prompt: true });
      const meta = out.meta;
      state.metas[`${slug}/${stage}`] = meta;
      applyMeta(cell, meta);
      await reloadStill(cell, slug, stage);
      setStatus(cell, "done", "ok");
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
      toast(e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  async function onAnimate(cell) {
    const { slug, stage } = cell.dataset;
    setBusy(cell, "animating…");
    setStatus(cell, "PixelLab is rendering 8 frames (30–90 s)…", "busy");
    try {
      const out = await api("POST",
        `/api/fishgen/${slug}/${stage}/animate`);
      state.metas[`${slug}/${stage}`] = out.meta;
      applyMeta(cell, out.meta);
      await refreshSheet(cell, slug, stage);
      setStatus(cell, `✓ ${out.frames} frames`, "ok");
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
      toast(e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  async function onSavePrompt(cell) {
    const { slug, stage } = cell.dataset;
    const prompt = $(".prompt", cell).value;
    setBusy(cell, "saving…");
    try {
      await api("POST",
        `/api/fishgen/${slug}/${stage}/prompt`, { prompt });
      setStatus(cell, "prompt saved", "ok");
      state.metas[`${slug}/${stage}`].prompt = prompt;
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  async function onResetPrompt(cell) {
    const { slug, stage } = cell.dataset;
    setBusy(cell, "loading default…");
    try {
      // Save an empty string locally then ask server for the default.
      // Server will return the default if no prompt.txt exists, but
      // we don't want to delete any saved prompt — just preview the
      // template. We hit a special case: temporarily clear value to
      // get default text.
      const r = await api("GET",
        `/api/fishgen/${slug}/${stage}/prompt`);
      // If the server says default=true, that's the template. If the
      // user has a saved one, we still load that — easier for them
      // to see what's saved. Pull "default" by clearing: skip server
      // call and use a hard-coded fallback path instead.
      if (r.default) {
        $(".prompt", cell).value = r.prompt;
        setStatus(cell, "loaded default", "ok");
      } else {
        // Force-fetch default by hitting the endpoint with a flag —
        // there's no such flag, so we approximate: hint the user.
        $(".prompt", cell).value = r.prompt;
        setStatus(cell, "loaded saved prompt (delete prompt.txt on disk to see fresh default)", "ok");
      }
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  function onTogglePrompt(cell) {
    const block = $(".prompt-block", cell);
    block.hidden = !block.hidden;
    if (!block.hidden) $(".prompt", cell).focus();
  }

  async function onWipe(cell) {
    const { slug, stage } = cell.dataset;
    if (!confirm(`Delete saved image + sheet for ${slug}/${stage}?`)) return;
    setBusy(cell, "deleting…");
    try {
      await api("DELETE", `/api/fishgen/${slug}/${stage}/image`);
      const meta = { ...state.metas[`${slug}/${stage}`],
                     has_image: false, has_sheet: false };
      state.metas[`${slug}/${stage}`] = meta;
      applyMeta(cell, meta);
      const img = $(".still", cell);
      img.hidden = true;
      $(".sheet", cell).hidden = true;
      stopSheetAnimation($(".sheet", cell));
      $(".placeholder", cell).hidden = false;
      setStatus(cell, "wiped", "ok");
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  // ─── Build the grid ──────────────────────────────────────────────────

  function buildHeaderRow() {
    const grid = $("#grid");
    const labels = ["", ...state.stages.map(s => s.label)];
    for (const t of labels) {
      const div = document.createElement("div");
      div.className = "col-header" + (t === "" ? " spacer" : "");
      div.textContent = t;
      grid.appendChild(div);
    }
  }

  function buildRow(species) {
    const grid = $("#grid");
    const label = document.createElement("div");
    label.className = "row-label";
    label.textContent = species.name;
    grid.appendChild(label);

    for (const stage of state.stages) {
      const meta = species.stages.find(m => m.stage === stage.key);
      const cell = document.importNode($("#cell-template").content, true)
                           .firstElementChild;
      cell.dataset.slug = species.slug;
      cell.dataset.stage = stage.key;
      $(".prompt", cell).value = meta.prompt || "";
      $(".generate", cell).addEventListener("click", () => onGenerate(cell));
      $(".animate", cell).addEventListener("click", () => onAnimate(cell));
      $(".save-prompt", cell).addEventListener("click", () => onSavePrompt(cell));
      $(".reset-prompt", cell).addEventListener("click", () => onResetPrompt(cell));
      $(".edit-prompt", cell).addEventListener("click", () => onTogglePrompt(cell));
      $(".wipe", cell).addEventListener("click", () => onWipe(cell));
      grid.appendChild(cell);
    }
  }

  function renderGrid() {
    const grid = $("#grid");
    grid.innerHTML = "";
    buildHeaderRow();
    for (const sp of state.species) buildRow(sp);
  }

  function paintBadge(id, ok, label) {
    const el = $("#" + id);
    el.classList.toggle("ok", ok);
    el.classList.toggle("bad", !ok);
    el.textContent = `${label}: ${ok ? "configured" : "missing"}`;
  }

  // ─── Boot ────────────────────────────────────────────────────────────

  async function boot() {
    try {
      const list = await api("GET", "/api/fishgen/list");
      state.species = list.species;
      state.stages = list.stages;
      state.openaiOk = list.openai_configured;
      state.pixellabOk = list.pixellab_configured;
      rebuildMetas();
      paintBadge("openai-status", state.openaiOk, "OpenAI");
      paintBadge("pixellab-status", state.pixellabOk, "PixelLab");
      renderGrid();
      // After the DOM is in place, lazily load existing images.
      const cells = $$(".cell");
      for (const cell of cells) loadCell(cell);
    } catch (e) {
      toast("failed to load: " + e.message, "err");
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
