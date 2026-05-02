/* Fish animator — upload a PNG, animate it, download the sheet. */

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = { species: [], stages: [], pixellabOk: false, anthropicOk: false, anthropicModel: "", defaultAnimatePrompt: "", metas: {} };
  // Per-cell conversation history: key = "slug/stage", value = [{role,content}]
  const histories = {};

  // ─── HTTP helpers ────────────────────────────────────────────────────

  async function api(method, path, body) {
    const opts = { method, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    if (r.status === 401) { window.location.href = "/"; throw new Error("not authenticated"); }
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    if (r.status === 204) return null;
    const ct = r.headers.get("content-type") || "";
    return ct.includes("application/json") ? await r.json() : await r.blob();
  }

  function toast(msg, kind = "") {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 3500);
  }

  // ─── Sprite-sheet animation ──────────────────────────────────────────

  function startSheetAnimation(canvas, blobUrl) {
    stopSheetAnimation(canvas);
    const img = new Image();
    img.onload = () => {
      const fW = img.height;
      if (!fW) return;
      const nFrames = Math.max(1, Math.round(img.width / fW));
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const cssSize = canvas.getBoundingClientRect().width || 200;
      canvas.width = Math.round(cssSize * dpr);
      canvas.height = Math.round(cssSize * dpr);
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = false;
      let idx = 0, last = 0;
      function tick(ts) {
        if (!canvas.isConnected) return;
        if (ts - last >= 1000 / 10) {
          last = ts;
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, idx * fW, 0, fW, fW, 0, 0, canvas.width, canvas.height);
          idx = (idx + 1) % nFrames;
        }
        canvas._raf = requestAnimationFrame(tick);
      }
      canvas._raf = requestAnimationFrame(tick);
    };
    img.onerror = () => stopSheetAnimation(canvas);
    img.src = blobUrl;
    canvas._blobUrl = blobUrl;
  }

  function stopSheetAnimation(canvas) {
    if (canvas._raf) cancelAnimationFrame(canvas._raf);
    canvas._raf = null;
    if (canvas._blobUrl) { try { URL.revokeObjectURL(canvas._blobUrl); } catch {} canvas._blobUrl = null; }
  }

  async function refreshSheet(cell, slug, stage) {
    const canvas = $(".sheet", cell);
    try {
      const r = await fetch(`/api/fishgen/${slug}/${stage}/sheet?t=${Date.now()}`, { credentials: "same-origin" });
      if (!r.ok) { canvas.hidden = true; return false; }
      const blob = await r.blob();
      canvas.hidden = false;
      $(".still", cell).hidden = true;
      $(".placeholder", cell).hidden = true;
      startSheetAnimation(canvas, URL.createObjectURL(blob));
      return true;
    } catch { canvas.hidden = true; return false; }
  }

  // ─── Cell state ──────────────────────────────────────────────────────

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
    stopSheetAnimation(canvas);
    canvas.hidden = true;
    try {
      const r = await fetch(`/api/fishgen/${slug}/${stage}/image?t=${Date.now()}`, { credentials: "same-origin" });
      if (!r.ok) { img.hidden = true; img.removeAttribute("src"); $(".placeholder", cell).hidden = false; return false; }
      const blob = await r.blob();
      const prevUrl = img._blobUrl;
      const url = URL.createObjectURL(blob);
      img.hidden = false;
      $(".placeholder", cell).hidden = true;
      img.src = url;
      img._blobUrl = url;
      if (prevUrl) { try { URL.revokeObjectURL(prevUrl); } catch {} }
      return true;
    } catch { return false; }
  }

  function applyMeta(cell, meta) {
    cell.dataset.hasImage = meta.has_image ? "1" : "";
    cell.dataset.hasSheet = meta.has_sheet ? "1" : "";
    $(".animate", cell).disabled = !meta.has_image || !state.pixellabOk;
    $(".download", cell).disabled = !meta.has_image && !meta.has_sheet;
    $(".wipe", cell).hidden = !meta.has_image && !meta.has_sheet;
  }

  async function loadCell(cell) {
    const { slug, stage } = cell.dataset;
    const meta = state.metas[`${slug}/${stage}`];
    applyMeta(cell, meta);
    if (meta.has_image) await reloadStill(cell, slug, stage);
    if (meta.has_sheet) await refreshSheet(cell, slug, stage);
  }

  // ─── Action handlers ─────────────────────────────────────────────────

  async function onUpload(cell, file) {
    if (!file || file.type !== "image/png") { toast("Please select a PNG file", "err"); return; }
    const { slug, stage } = cell.dataset;
    setBusy(cell, "uploading…");
    try {
      const buf = await file.arrayBuffer();
      const r = await fetch(`/api/fishgen/${slug}/${stage}/upload`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "image/png" },
        body: buf,
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
      const out = await r.json();
      state.metas[`${slug}/${stage}`] = out.meta;
      applyMeta(cell, out.meta);
      await reloadStill(cell, slug, stage);
      setStatus(cell, "uploaded — click Animate", "ok");
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
      toast(e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  async function onAnimate(cell) {
    const { slug, stage } = cell.dataset;
    const action = $(".animate-prompt", cell).value.trim();
    setBusy(cell, "animating…");
    setStatus(cell, "PixelLab rendering 8 frames (30–90 s)…", "busy");
    try {
      const out = await api("POST", `/api/fishgen/${slug}/${stage}/animate`, { action });
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

  async function onDownload(cell) {
    const { slug, stage } = cell.dataset;
    const meta = state.metas[`${slug}/${stage}`];
    // Try sheet first if meta says it exists; fall back to image on 404
    // (covers stale meta where the sheet was deleted server-side).
    const candidates = [];
    if (meta && meta.has_sheet) candidates.push({ kind: "sheet", url: `/api/fishgen/${slug}/${stage}/sheet` });
    candidates.push({ kind: "image", url: `/api/fishgen/${slug}/${stage}/image` });
    try {
      let blob = null, kind = "image";
      for (const c of candidates) {
        const r = await fetch(c.url, { credentials: "same-origin" });
        if (r.ok) { blob = await r.blob(); kind = c.kind; break; }
        if (r.status !== 404) throw new Error(r.statusText);
      }
      if (!blob) throw new Error("nothing to download");
      const filename = `${slug}-${stage}${kind === "sheet" ? "-sheet" : ""}.png`;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      toast("Download failed: " + e.message, "err");
    }
  }

  async function onSuggestPrompt(cell) {
    const { slug, stage } = cell.dataset;
    setBusy(cell, "asking Claude…");
    setStatus(cell, "drafting prompt (5–15 s)…", "busy");
    try {
      const out = await api("POST", `/api/fishgen/${slug}/${stage}/suggest_prompt`);
      const prompt = out.prompt || "";
      $(".prompt", cell).value = prompt;
      // Seed the conversation history with this as the first assistant turn
      const key = `${slug}/${stage}`;
      histories[key] = [
        { role: "user", content: `Write a ${stage} prompt for ${SLUG_TO_NAME[slug] || slug}.` },
        { role: "assistant", content: prompt },
      ];
      $(".refine-log", cell).innerHTML = "";
      setStatus(cell, "✓ prompt ready — paste into OpenAI platform", "ok");
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
      toast(e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  async function onRefinePrompt(cell) {
    const { slug, stage } = cell.dataset;
    const feedback = $(".refine-input", cell).value.trim();
    if (!feedback) return;
    const currentPrompt = $(".prompt", cell).value.trim();
    if (!currentPrompt) { toast("Add a prompt first", "err"); return; }

    const key = `${slug}/${stage}`;
    // Build history: if none yet, seed with the current prompt as first assistant turn
    if (!histories[key] || !histories[key].length) {
      histories[key] = [
        { role: "user", content: `Write a ${stage} prompt for ${SLUG_TO_NAME[slug] || slug}.` },
        { role: "assistant", content: currentPrompt },
      ];
    }
    // Append user feedback
    histories[key].push({ role: "user", content: feedback });

    setBusy(cell, "refining…");
    setStatus(cell, "Claude is tweaking the prompt…", "busy");
    try {
      const out = await api("POST", `/api/fishgen/${slug}/${stage}/refine_prompt`, { history: histories[key] });
      const refined = out.prompt || "";
      // Append assistant response to history
      histories[key].push({ role: "assistant", content: refined });
      $(".prompt", cell).value = refined;
      $(".refine-input", cell).value = "";
      // Append to log
      const log = $(".refine-log", cell);
      const entry = document.createElement("div");
      entry.className = "refine-entry";
      entry.innerHTML = `<span class="refine-tag">you</span>${escHtml(feedback)}`;
      log.appendChild(entry);
      log.scrollTop = log.scrollHeight;
      setStatus(cell, "✓ prompt refined", "ok");
    } catch (e) {
      // Roll back the user message we optimistically pushed
      histories[key].pop();
      setStatus(cell, "✗ " + e.message, "err");
      toast(e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  function escHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Lookup slug → display name from loaded species list
  const SLUG_TO_NAME = {};

  async function onWipe(cell) {
    const { slug, stage } = cell.dataset;
    if (!confirm(`Delete image + sheet for ${slug}/${stage}?`)) return;
    setBusy(cell, "deleting…");
    try {
      await api("DELETE", `/api/fishgen/${slug}/${stage}/image`);
      const meta = { ...state.metas[`${slug}/${stage}`], has_image: false, has_sheet: false };
      state.metas[`${slug}/${stage}`] = meta;
      applyMeta(cell, meta);
      $(".still", cell).hidden = true;
      stopSheetAnimation($(".sheet", cell));
      $(".sheet", cell).hidden = true;
      $(".placeholder", cell).hidden = false;
      setStatus(cell, "deleted", "ok");
    } catch (e) {
      setStatus(cell, "✗ " + e.message, "err");
    } finally {
      setBusy(cell, "");
    }
  }

  // ─── Build grid ──────────────────────────────────────────────────────

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
      const cell = document.importNode($("#cell-template").content, true).firstElementChild;
      cell.dataset.slug = species.slug;
      cell.dataset.stage = stage.key;

      const preview = $(".preview", cell);
      const fileInput = $(".file-input", cell);

      // Click preview to pick file
      preview.addEventListener("click", (e) => {
        if (e.target === fileInput) return;
        fileInput.click();
      });
      fileInput.addEventListener("change", () => {
        if (fileInput.files[0]) onUpload(cell, fileInput.files[0]);
        fileInput.value = "";
      });

      // Drag-and-drop
      preview.addEventListener("dragover", (e) => { e.preventDefault(); preview.classList.add("drag-over"); });
      preview.addEventListener("dragleave", () => preview.classList.remove("drag-over"));
      preview.addEventListener("drop", (e) => {
        e.preventDefault();
        preview.classList.remove("drag-over");
        onUpload(cell, e.dataTransfer.files[0]);
      });

      $(".suggest-prompt", cell).addEventListener("click", () => onSuggestPrompt(cell));
      $(".refine-btn", cell).addEventListener("click", () => onRefinePrompt(cell));
      $(".refine-input", cell).addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onRefinePrompt(cell); }
      });
      $(".animate-prompt", cell).value = state.defaultAnimatePrompt;
      $(".animate", cell).addEventListener("click", () => onAnimate(cell));
      $(".download", cell).addEventListener("click", () => onDownload(cell));
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

  // ─── Boot ────────────────────────────────────────────────────────────

  async function boot() {
    try {
      const list = await api("GET", "/api/fishgen/list");
      state.species = list.species;
      state.stages = list.stages;
      state.pixellabOk = list.pixellab_configured;
      state.anthropicOk = list.anthropic_configured;
      state.anthropicModel = list.anthropic_model || "Claude";
      state.defaultAnimatePrompt = list.default_animate_prompt || "";
      state.metas = {};
      for (const sp of list.species) {
        SLUG_TO_NAME[sp.slug] = sp.name;
        for (const m of sp.stages)
          state.metas[`${m.slug}/${m.stage}`] = m;
      }

      const pl = $("#pixellab-status");
      pl.classList.toggle("ok", state.pixellabOk);
      pl.classList.toggle("bad", !state.pixellabOk);
      pl.textContent = `PixelLab: ${state.pixellabOk ? "ready" : "missing"}`;

      const cl = $("#anthropic-status");
      cl.classList.toggle("ok", state.anthropicOk);
      cl.classList.toggle("bad", !state.anthropicOk);
      cl.textContent = state.anthropicOk ? `Claude (${state.anthropicModel})` : "Claude: missing";

      renderGrid();
      for (const cell of $$(".cell")) loadCell(cell);
    } catch (e) {
      toast("failed to load: " + e.message, "err");
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
