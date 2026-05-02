(async function () {
  // Tab switching
  const HSK_PASSWORD = "michelle";
  const tabs = document.querySelectorAll(".tab");
  const panels = ["words", "exam", "hsk", "fish"];

  tabs.forEach(t => t.addEventListener("click", () => {
    if (t.dataset.tab === "hsk" && sessionStorage.getItem("hskUnlocked") !== "1") {
      const entered = window.prompt("Enter password for HSK Browser:");
      if (entered !== HSK_PASSWORD) { if (entered !== null) window.alert("Wrong password."); return; }
      sessionStorage.setItem("hskUnlocked", "1");
    }
    if (t.dataset.tab === "fish") { window.location.href = "/fishgen"; return; }
    tabs.forEach(x => x.classList.toggle("active", x === t));
    panels.forEach(p => { document.getElementById("panel-" + p).hidden = t.dataset.tab !== p; });
  }));

  // Populate voice dropdowns from server defaults
  const defaults = await fetch("/api/defaults").then(r => r.json());
  const fill = (id, opts, selected) => {
    const el = document.getElementById(id);
    el.innerHTML = "";
    opts.forEach(v => {
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      if (v === selected) o.selected = true;
      el.appendChild(o);
    });
  };
  fill("w-voice", [...defaults.female_voices, ...defaults.male_voices], "Serena");
  fill("e-female", defaults.female_voices, "Serena");
  fill("e-male", defaults.male_voices, "Ethan");
  fill("h-voice", ["Cherry"], "Cherry");

  const keyHint = defaults.has_api_key
    ? "(server has default — leave blank)"
    : "(required — server has no default)";
  document.getElementById("w-key-hint").textContent = keyHint;
  document.getElementById("e-key-hint").textContent = keyHint;

  // Generic job submit + poll
  async function submitJob(endpoint, form, progressEl, barEl, statusEl, dlEl) {
    const fd = new FormData(form);
    progressEl.hidden = false;
    dlEl.hidden = true;
    barEl.style.width = "0%";
    statusEl.textContent = "Uploading…";

    let resp;
    try {
      resp = await fetch(endpoint, { method: "POST", body: fd });
    } catch (e) {
      statusEl.textContent = "Upload failed: " + e.message;
      return;
    }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      statusEl.textContent = "Error: " + (err.detail || resp.statusText);
      return;
    }
    const { job_id } = await resp.json();
    statusEl.textContent = "Queued…";

    while (true) {
      await new Promise(r => setTimeout(r, 1200));
      const j = await fetch(`/api/jobs/${job_id}`).then(r => r.json());
      const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
      barEl.style.width = pct + "%";
      if (j.status === "running" || j.status === "pending") {
        statusEl.textContent = `Processing ${j.done}/${j.total}` +
          (j.current ? ` — ${j.current}` : "") + ` (${pct}%)`;
      } else if (j.status === "done") {
        statusEl.textContent = `Done — ${j.done}/${j.total} items. Click below to download.`;
        dlEl.hidden = false;
        dlEl.href = `/api/jobs/${job_id}/download`;
        dlEl.textContent = `Download ${j.filename}`;
        return;
      } else {
        statusEl.textContent = "Failed: " + (j.error || "unknown error");
        return;
      }
    }
  }

  document.getElementById("form-words").addEventListener("submit", ev => {
    ev.preventDefault();
    submitJob("/api/words", ev.target,
      document.getElementById("progress-words"),
      document.getElementById("bar-words"),
      document.getElementById("status-words"),
      document.getElementById("dl-words"));
  });
  document.getElementById("form-exam").addEventListener("submit", ev => {
    ev.preventDefault();
    submitJob("/api/exam", ev.target,
      document.getElementById("progress-exam"),
      document.getElementById("bar-exam"),
      document.getElementById("status-exam"),
      document.getElementById("dl-exam"));
  });

  // ===== HSK Browser =====
  const hLevel   = document.getElementById("h-level");
  const hLesson  = document.getElementById("h-lesson");
  const hStatus  = document.getElementById("h-status");
  const hWords   = document.getElementById("h-words");
  const hVoice   = document.getElementById("h-voice");

  function setHStatus(msg, isErr) {
    hStatus.textContent = msg || "";
    hStatus.style.color = isErr ? "var(--err)" : "var(--muted)";
  }

  async function fetchJson(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  }

  hLevel.addEventListener("change", async () => {
    hLesson.innerHTML = '<option value="">—</option>';
    hWords.innerHTML = "";
    const level = hLevel.value;
    if (!level) return;
    setHStatus("Loading lessons…");
    try {
      const { lessons } = await fetchJson(`/api/hsk/lessons?level=${encodeURIComponent(level)}`);
      lessons.forEach(l => {
        const o = document.createElement("option");
        o.value = l._id;
        const idx = l.topicIndex != null ? `${l.topicIndex}. ` : "";
        o.textContent = `${idx}${l.topicTitle} — ${l.englishTitle || ""} (${l.wordCount})`;
        hLesson.appendChild(o);
      });
      setHStatus(`${lessons.length} lessons`);
    } catch (e) {
      setHStatus("Failed: " + e.message, true);
    }
  });

  hLesson.addEventListener("change", async () => {
    hWords.innerHTML = "";
    const lessonId = hLesson.value;
    if (!lessonId) return;
    setHStatus("Loading words…");
    try {
      const data = await fetchJson(`/api/hsk/lessons/${encodeURIComponent(lessonId)}/words`);
      renderWords(lessonId, data.words);
      setHStatus(`${data.words.length} words in ${data.topicTitle}`);
    } catch (e) {
      setHStatus("Failed: " + e.message, true);
    }
  });

  function renderWords(_lessonId, words) {
    hWords.innerHTML = "";
    words.forEach(w => hWords.appendChild(renderWordRow(w)));
  }

  function renderWordRow(w) {
    const row = document.createElement("div");
    row.className = "wordrow";

    // Col 1: hanzi + play
    const col1 = document.createElement("div");
    const hanzi = document.createElement("div");
    hanzi.className = "hanzi";
    hanzi.textContent = w.chinese;
    col1.appendChild(hanzi);

    if (!w.wordId) {
      const missing = document.createElement("div");
      missing.className = "muted";
      missing.style.fontSize = "12px";
      missing.textContent = "not found in words collection";
      col1.appendChild(missing);
      row.appendChild(col1);
      return row;
    }

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.style.width = "100%";
    audio.style.marginTop = "6px";
    const audioUrl = `/api/hsk/words/${encodeURIComponent(w.wordId)}/audio`;
    if (w.hasAudio) {
      audio.src = audioUrl;
    } else {
      const noAudio = document.createElement("div");
      noAudio.className = "muted";
      noAudio.style.fontSize = "12px";
      noAudio.textContent = "no audio yet";
      col1.appendChild(noAudio);
    }
    col1.appendChild(audio);
    row.appendChild(col1);

    // Col 2: pinyin select
    const col2 = document.createElement("div");
    const pLabel = document.createElement("label");
    pLabel.textContent = "Pinyin";
    const pSelect = document.createElement("select");
    const opts = w.pinyinOptions && w.pinyinOptions.length ? w.pinyinOptions : [w.pinyin];
    opts.forEach(p => {
      const o = document.createElement("option");
      o.value = p; o.textContent = p;
      if (p === w.pinyin) o.selected = true;
      pSelect.appendChild(o);
    });
    // Also allow free-text entry
    const pCustom = document.createElement("input");
    pCustom.type = "text";
    pCustom.placeholder = "or type custom pinyin";
    pCustom.style.marginTop = "4px";
    pLabel.appendChild(pSelect);
    col2.appendChild(pLabel);
    col2.appendChild(pCustom);
    row.appendChild(col2);

    // Col 3: meaning
    const col3 = document.createElement("div");
    const mLabel = document.createElement("label");
    mLabel.textContent = "Meaning";
    const mInput = document.createElement("input");
    mInput.type = "text";
    mInput.value = w.english || "";
    mLabel.appendChild(mInput);
    col3.appendChild(mLabel);
    const rowStatus = document.createElement("div");
    rowStatus.className = "row-status";
    col3.appendChild(rowStatus);
    row.appendChild(col3);

    // Col 4: actions
    const col4 = document.createElement("div");
    col4.className = "actions";
    const regenBtn = document.createElement("button");
    regenBtn.type = "button";
    regenBtn.textContent = "Regenerate audio";
    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "btn-secondary";
    saveBtn.textContent = "Save meaning/pinyin";
    col4.appendChild(regenBtn);
    col4.appendChild(saveBtn);
    row.appendChild(col4);

    function selectedPinyin() {
      return (pCustom.value.trim() || pSelect.value || w.pinyin || "").trim();
    }

    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      rowStatus.textContent = "Saving…";
      try {
        const body = {
          pinyin: selectedPinyin(),
          english: mInput.value.trim(),
        };
        const updated = await fetchJson(
          `/api/hsk/words/${encodeURIComponent(w.wordId)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
        );
        w.pinyin = updated.pinyin ?? body.pinyin;
        w.english = updated.english ?? body.english;
        rowStatus.textContent = "Saved.";
      } catch (e) {
        rowStatus.textContent = "Save failed: " + e.message;
        rowStatus.style.color = "var(--err)";
      } finally {
        saveBtn.disabled = false;
        setTimeout(() => { rowStatus.textContent = ""; rowStatus.style.color = ""; }, 3500);
      }
    });

    regenBtn.addEventListener("click", async () => {
      regenBtn.disabled = true;
      rowStatus.textContent = "Generating…";
      try {
        const body = {
          pinyin: selectedPinyin(),
          voice: hVoice.value,
        };
        const res = await fetchJson(
          `/api/hsk/words/${encodeURIComponent(w.wordId)}/regenerate`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
        );
        w.pinyin = res.pinyin || w.pinyin;
        // Bust cache on the audio element
        audio.src = audioUrl + `?t=${Date.now()}`;
        rowStatus.textContent = `Regenerated (${Math.round(res.bytes / 1024)} KB).`;
      } catch (e) {
        rowStatus.textContent = "Regen failed: " + e.message;
        rowStatus.style.color = "var(--err)";
      } finally {
        regenBtn.disabled = false;
        setTimeout(() => { rowStatus.textContent = ""; rowStatus.style.color = ""; }, 4000);
      }
    });

    return row;
  }

  // ===== Fish Studio =====
  const FISH_POSITIONS = [
    { x:  8, y: 22 }, { x: 36, y: 15 }, { x: 63, y: 28 }, { x: 82, y: 12 },
    { x: 15, y: 50 }, { x: 46, y: 43 }, { x: 70, y: 55 }, { x: 87, y: 40 },
    { x:  6, y: 72 }, { x: 32, y: 66 }, { x: 58, y: 76 }, { x: 76, y: 68 },
  ];
  const FPS = 12;
  let fsLoaded = false;
  let fsSelectedId = null;

  function fsSetSelected(id) {
    fsSelectedId = id;
    const dlBtn = document.getElementById("fs-dl-btn");
    if (dlBtn) dlBtn.disabled = !id;
  }

  async function fsLoadExisting() {
    try {
      const { sprites } = await fetchJson("/api/sprite/list");
      sprites.forEach(meta => fsAddFish(meta));
    } catch (e) {
      document.getElementById("fs-status").textContent = "Failed to load sprites: " + e.message;
    }
  }

  function fsNextPos() {
    const water = document.getElementById("fs-water");
    const count = water.querySelectorAll(".fs-fish-wrap, .fs-fish-skeleton").length;
    return FISH_POSITIONS[count % FISH_POSITIONS.length];
  }

  function fsAddSkeleton() {
    const pos = fsNextPos();
    const sk = document.createElement("div");
    sk.className = "fs-fish-skeleton";
    sk.style.left  = pos.x + "%";
    sk.style.top   = pos.y + "%";
    document.getElementById("fs-water").appendChild(sk);
    return sk;
  }

  function fsAddFish(meta) {
    const water = document.getElementById("fs-water");
    const pos = FISH_POSITIONS[water.querySelectorAll(".fs-fish-wrap").length % FISH_POSITIONS.length];

    const wrap = document.createElement("div");
    wrap.className = "fs-fish-wrap";
    wrap.dataset.id = meta.id;
    wrap.style.left = pos.x + "%";
    wrap.style.top  = pos.y + "%";

    const canvas = document.createElement("canvas");
    const DISP = 96;
    canvas.width  = DISP;
    canvas.height = DISP;
    wrap.appendChild(canvas);

    const delBtn = document.createElement("button");
    delBtn.className = "fs-fish-delete";
    delBtn.textContent = "✕";
    delBtn.title = "Delete";
    wrap.appendChild(delBtn);

    water.appendChild(wrap);
    fsAnimate(canvas, meta);

    wrap.addEventListener("click", (e) => {
      if (e.target === delBtn) return;
      const id = wrap.dataset.id;
      if (fsSelectedId === id) {
        wrap.classList.remove("selected");
        fsSetSelected(null);
      } else {
        document.querySelectorAll(".fs-fish-wrap.selected").forEach(w => w.classList.remove("selected"));
        wrap.classList.add("selected");
        fsSetSelected(id);
      }
    });

    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      delBtn.disabled = true;
      try {
        await fetch("/api/sprite/" + meta.id, { method: "DELETE" });
        wrap.remove();
        if (fsSelectedId === meta.id) fsSetSelected(null);
      } catch (err) {
        delBtn.disabled = false;
      }
    });

    // Deselect on outside click
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".fs-fish-wrap") && !e.target.closest("#fs-dl-btn")) {
        document.querySelectorAll(".fs-fish-wrap.selected").forEach(w => w.classList.remove("selected"));
        fsSetSelected(null);
      }
    }, { capture: false });

    return wrap;
  }

  function fsAnimate(canvas, meta) {
    const ctx = canvas.getContext("2d");
    const img = new Image();
    img.src = "/api/sprite/" + meta.id + "/image?t=" + Date.now();

    let frame = 1;  // frame 0 is the static reference — skip it in animation
    let last = 0;
    const interval = 1000 / FPS;
    let cancelled = false;
    canvas._cancelAnim = () => { cancelled = true; };

    function draw(ts) {
      if (cancelled) return;
      requestAnimationFrame(draw);
      if (!img.complete || img.naturalWidth === 0) return;
      if (ts - last < interval) return;
      last = ts;

      const cols  = meta.cols, rows = meta.rows;
      const total = meta.total || (cols * rows);
      const col   = frame % cols;
      const row   = Math.floor(frame / cols);
      const sw    = img.naturalWidth  / cols;
      const sh    = img.naturalHeight / rows;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, col * sw, row * sh, sw, sh, 0, 0, canvas.width, canvas.height);
      frame = (frame % (total - 1)) + 1;  // loop 1..total-1, never hit frame 0
    }
    requestAnimationFrame(draw);
  }

  // Download selected sprite sheet
  document.getElementById("fs-dl-btn").addEventListener("click", async () => {
    if (!fsSelectedId) return;
    const url = "/api/sprite/" + fsSelectedId + "/image?t=" + Date.now();
    const a = document.createElement("a");
    a.href = url;
    a.download = "sprite_" + fsSelectedId + ".png";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });

  // Upload + generate
  const fsDropZone  = document.getElementById("fs-drop-zone");
  const fsFileInput = document.getElementById("fs-file-input");
  const fsGenBtn    = document.getElementById("fs-gen-btn");
  const fsStatus    = document.getElementById("fs-status");
  const fsPreview   = document.getElementById("fs-preview");
  const fsDropLabel = document.getElementById("fs-drop-label");
  let fsPendingFile = null;

  function fsSetFile(file) {
    if (!file || file.type !== "image/png") { fsStatus.textContent = "Please select a PNG file."; return; }
    fsPendingFile = file;
    const url = URL.createObjectURL(file);
    fsPreview.src = url;
    fsPreview.hidden = false;
    fsDropLabel.textContent = file.name;
    fsGenBtn.disabled = false;
    fsStatus.textContent = "";
  }

  fsDropZone.addEventListener("click", () => fsFileInput.click());
  fsFileInput.addEventListener("change", () => fsSetFile(fsFileInput.files[0]));
  fsDropZone.addEventListener("dragover", e => { e.preventDefault(); fsDropZone.classList.add("drag-over"); });
  fsDropZone.addEventListener("dragleave", () => fsDropZone.classList.remove("drag-over"));
  fsDropZone.addEventListener("drop", e => {
    e.preventDefault();
    fsDropZone.classList.remove("drag-over");
    fsSetFile(e.dataTransfer.files[0]);
  });

  fsGenBtn.addEventListener("click", async () => {
    if (!fsPendingFile) return;
    fsGenBtn.disabled = true;
    fsStatus.textContent = "Submitting…";
    fsStatus.className = "fs-status";

    const fd = new FormData();
    fd.append("file", fsPendingFile);

    let jobId;
    try {
      const r = await fetch("/api/sprite/generate", { method: "POST", body: fd });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
      jobId = (await r.json()).job_id;
    } catch (e) {
      fsStatus.textContent = "Error: " + e.message;
      fsStatus.className = "fs-status err";
      fsGenBtn.disabled = false;
      return;
    }

    // Add 3 skeleton placeholders
    const skeletons = [fsAddSkeleton(), fsAddSkeleton(), fsAddSkeleton()];
    let skIdx = 0;

    // Poll
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      let j;
      try { j = await fetchJson("/api/sprite/jobs/" + jobId); }
      catch { break; }

      fsStatus.textContent = `Generating… ${j.done}/${j.total}`;

      // Swap each newly completed sprite in for a skeleton
      while (skIdx < j.completed.length) {
        const meta = j.completed[skIdx];
        const sk = skeletons[skIdx];
        if (sk && sk.parentNode) sk.remove();
        fsAddFish(meta);
        skIdx++;
      }

      if (j.status === "done" || j.status === "error") {
        // Remove any remaining skeletons
        skeletons.forEach(s => { if (s.parentNode) s.remove(); });
        if (j.error && skIdx === 0) {
          fsStatus.textContent = "Error: " + j.error;
          fsStatus.className = "fs-status err";
        } else {
          fsStatus.textContent = `Done — ${j.done} sprite${j.done !== 1 ? "s" : ""} generated.`;
        }
        fsGenBtn.disabled = false;
        break;
      }
    }
  });

  // ===== Test sprite-sheet uploader =====
  // Drop a PNG, choose grid, watch it animate in place — same draw loop as
  // generated sheets. Lets us verify whether OpenAI image-edit output is
  // structurally correct (frame layout, alignment) without a full round-trip.
  const fsTestDrop   = document.getElementById("fs-test-drop");
  const fsTestInput  = document.getElementById("fs-test-input");
  const fsTestLabel  = document.getElementById("fs-test-label");
  const fsTestColsEl = document.getElementById("fs-test-cols");
  const fsTestRowsEl = document.getElementById("fs-test-rows");
  const fsTestClear  = document.getElementById("fs-test-clear");
  const fsTestCanvas = document.getElementById("fs-test-canvas");
  let fsTestImg = null;
  let fsTestUrl = null;
  let fsTestCancel = null;

  function fsTestStart() {
    if (!fsTestImg) return;
    if (fsTestCancel) fsTestCancel();
    const ctx = fsTestCanvas.getContext("2d");
    let frame = 1;             // skip frame 0 (static reference)
    let last  = 0;
    const interval = 1000 / FPS;
    let cancelled = false;
    fsTestCancel = () => { cancelled = true; };
    function draw(ts) {
      if (cancelled) return;
      requestAnimationFrame(draw);
      if (!fsTestImg.complete || fsTestImg.naturalWidth === 0) return;
      if (ts - last < interval) return;
      last = ts;
      const cols  = Math.max(1, parseInt(fsTestColsEl.value, 10) || 4);
      const rows  = Math.max(1, parseInt(fsTestRowsEl.value, 10) || 4);
      const total = cols * rows;
      const col   = frame % cols;
      const row   = Math.floor(frame / cols);
      const sw    = fsTestImg.naturalWidth  / cols;
      const sh    = fsTestImg.naturalHeight / rows;
      ctx.clearRect(0, 0, fsTestCanvas.width, fsTestCanvas.height);
      ctx.drawImage(fsTestImg, col * sw, row * sh, sw, sh,
                    0, 0, fsTestCanvas.width, fsTestCanvas.height);
      frame = total > 1 ? (frame % (total - 1)) + 1 : 0;
    }
    requestAnimationFrame(draw);
  }

  function fsTestSetFile(file) {
    if (!file || file.type !== "image/png") {
      fsStatus.textContent = "Test sheet must be a PNG.";
      return;
    }
    if (fsTestUrl) URL.revokeObjectURL(fsTestUrl);
    fsTestUrl = URL.createObjectURL(file);
    fsTestImg = new Image();
    fsTestImg.onload = () => fsTestStart();
    fsTestImg.src = fsTestUrl;
    fsTestLabel.textContent = file.name;
    fsTestClear.disabled = false;
  }

  fsTestDrop.addEventListener("click", () => fsTestInput.click());
  fsTestInput.addEventListener("change", () => fsTestSetFile(fsTestInput.files[0]));
  fsTestDrop.addEventListener("dragover",  e => { e.preventDefault(); fsTestDrop.classList.add("drag-over"); });
  fsTestDrop.addEventListener("dragleave", () => fsTestDrop.classList.remove("drag-over"));
  fsTestDrop.addEventListener("drop", e => {
    e.preventDefault();
    fsTestDrop.classList.remove("drag-over");
    fsTestSetFile(e.dataTransfer.files[0]);
  });
  // Live re-grid when cols/rows change
  fsTestColsEl.addEventListener("input", fsTestStart);
  fsTestRowsEl.addEventListener("input", fsTestStart);
  fsTestClear.addEventListener("click", () => {
    if (fsTestCancel) fsTestCancel();
    if (fsTestUrl) { URL.revokeObjectURL(fsTestUrl); fsTestUrl = null; }
    fsTestImg = null;
    fsTestInput.value = "";
    fsTestLabel.textContent = "Test sprite sheet — drop PNG or ";
    const browse = document.createElement("label");
    browse.htmlFor = "fs-test-input";
    browse.className = "fs-browse";
    browse.textContent = "browse";
    fsTestLabel.appendChild(browse);
    fsTestCanvas.getContext("2d").clearRect(0, 0, fsTestCanvas.width, fsTestCanvas.height);
    fsTestClear.disabled = true;
  });
})();
