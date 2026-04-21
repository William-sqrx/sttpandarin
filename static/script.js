(async function () {
  // Tab switching
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach(t => t.addEventListener("click", () => {
    tabs.forEach(x => x.classList.toggle("active", x === t));
    document.getElementById("panel-words").hidden = t.dataset.tab !== "words";
    document.getElementById("panel-exam").hidden = t.dataset.tab !== "exam";
    document.getElementById("panel-hsk").hidden = t.dataset.tab !== "hsk";
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
  fill("h-voice", [...defaults.female_voices, ...defaults.male_voices], "Serena");

  const keyHint = defaults.has_api_key
    ? "(server has default — leave blank)"
    : "(required — server has no default)";
  document.getElementById("w-key-hint").textContent = keyHint;
  document.getElementById("e-key-hint").textContent = keyHint;
  document.getElementById("h-key-hint").textContent = keyHint;

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
  const hSpeed   = document.getElementById("h-speed");
  const hApiKey  = document.getElementById("h-api-key");

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

  function renderWords(lessonId, words) {
    hWords.innerHTML = "";
    words.forEach(w => hWords.appendChild(renderWordRow(lessonId, w)));
  }

  function renderWordRow(lessonId, w) {
    const row = document.createElement("div");
    row.className = "wordrow";

    // Col 1: hanzi + play
    const col1 = document.createElement("div");
    const hanzi = document.createElement("div");
    hanzi.className = "hanzi";
    hanzi.textContent = w.chinese;
    col1.appendChild(hanzi);
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.style.width = "100%";
    audio.style.marginTop = "6px";
    const audioUrl = `/api/hsk/lessons/${encodeURIComponent(lessonId)}/words/${w.index}/audio`;
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
          `/api/hsk/lessons/${encodeURIComponent(lessonId)}/words/${w.index}`,
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
          speed: parseFloat(hSpeed.value || "1.0"),
          api_key: hApiKey.value,
        };
        const res = await fetchJson(
          `/api/hsk/lessons/${encodeURIComponent(lessonId)}/words/${w.index}/regenerate`,
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
})();
