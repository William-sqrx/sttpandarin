// soundmap.js — reader/editor for the pinyin → familiar-word sound map.
//
// Reads/writes window.SoundMap's tables IN PLACE (so the live tester, which
// calls SoundMap.pinyinToSoundMap, always reflects edits), persists edits to
// localStorage, and can export the tables back as JS / JSON for the app.

(function () {
  const SM = window.SoundMap;
  const STORAGE_KEY = "chinesely_soundmap_overrides_v1";

  // Pristine defaults (deep clone taken BEFORE any saved overrides are applied).
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const DEFAULTS = {
    initials: clone(SM.INITIALS),
    finals: clone(SM.FINALS),
    syllables: clone(SM.SYLLABLES),
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // ── seg helpers ────────────────────────────────────────────────────────────
  // A sound-alike is { word, pre, hit, post } with word === pre+hit+post; `hit`
  // is the underlined matching sound. The editor stores word + hit and derives
  // pre/post by locating hit inside word.
  function buildSeg(word, hit) {
    word = (word || "").trim();
    hit = (hit || "").trim();
    if (!word) return { seg: null, badHit: false };
    const idx = hit ? word.indexOf(hit) : -1;
    if (hit && idx >= 0) {
      return {
        seg: { word, pre: word.slice(0, idx), hit, post: word.slice(idx + hit.length) },
        badHit: false,
      };
    }
    // No hit, or the typed sound isn't inside the word → no underline; flag it
    // red if a sound was typed but doesn't occur in the word.
    return { seg: { word, pre: word, hit: "", post: "" }, badHit: !!hit };
  }

  function previewHTML(seg) {
    if (!seg || !seg.word) return '<span class="sm-preview sm-none">— none —</span>';
    return (
      '<span class="sm-preview">' +
      esc(seg.pre) +
      (seg.hit ? '<span class="hit">' + esc(seg.hit) + "</span>" : "") +
      esc(seg.post) +
      "</span>"
    );
  }

  // ── persistence ────────────────────────────────────────────────────────────
  function snapshot() {
    return {
      initials: SM.INITIALS,
      finals: SM.FINALS,
      syllables: SM.SYLLABLES,
    };
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot()));
      flashSaved("Saved in this browser");
    } catch (e) {
      flashSaved("Couldn't save: " + e.message, true);
    }
  }

  // Apply a stored/default snapshot onto the live tables IN PLACE, so the
  // pinyinToSoundMap closure keeps seeing the same object references.
  function applySnapshot(snap) {
    const copyEntry = (live, saved) => {
      if (!live || !saved) return;
      live.en = saved.en ? clone(saved.en) : live.en;
      live.id = saved.id ? clone(saved.id) : undefined;
    };
    if (snap.initials) for (const k in SM.INITIALS) copyEntry(SM.INITIALS[k], snap.initials[k]);
    if (snap.finals) for (const k in SM.FINALS) copyEntry(SM.FINALS[k], snap.finals[k]);
    if (snap.syllables) {
      for (const k in SM.SYLLABLES) {
        const savedArr = snap.syllables[k];
        if (!savedArr) continue;
        SM.SYLLABLES[k].forEach((seg, i) => copyEntry(seg, savedArr[i]));
      }
    }
  }

  function loadSaved() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) applySnapshot(JSON.parse(raw));
    } catch (e) {
      /* ignore corrupt storage */
    }
  }

  let savedTimer = null;
  function flashSaved(msg, isErr) {
    const el = $("sm-saved");
    el.textContent = msg;
    el.hidden = false;
    el.style.color = isErr ? "var(--err)" : "#168a4a";
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { el.hidden = true; }, 1600);
  }

  // ── editable table rendering ────────────────────────────────────────────────
  const LANGS = [
    { key: "en", label: "English" },
    { key: "id", label: "Bahasa" },
  ];

  function langCell(entry, lang, dirty) {
    const seg = entry[lang];
    const word = seg ? seg.word : "";
    const hit = seg ? seg.hit : "";
    const sep = lang === "id" ? " sm-langsep" : "";
    return (
      '<td class="sm-in-word' + sep + '"><input type="text" data-lang="' + lang +
      '" data-field="word" value="' + esc(word) + '" placeholder="' +
      (lang === "id" ? "(optional)" : "word") + '"></td>' +
      '<td><input type="text" class="sm-in-hit" data-lang="' + lang +
      '" data-field="hit" value="' + esc(hit) + '" placeholder="sound"></td>' +
      '<td class="sm-pv" data-lang="' + lang + '">' + previewHTML(seg) + "</td>"
    );
  }

  function headerRow() {
    return (
      "<thead><tr><th>Key</th>" +
      LANGS.map(
        (l) =>
          "<th" + (l.key === "id" ? ' class="sm-langsep"' : "") + ">" + l.label + " word</th>" +
          "<th>sound</th><th>preview</th>"
      ).join("") +
      "</tr></thead>"
    );
  }

  // Render an editable table for INITIALS / FINALS (entry = { en, id }) or for
  // SYLLABLES (each row is one seg of a syllable's array).
  function renderTable(tableEl, rows) {
    // rows: [{ table, key, segIndex, entry, keyLabel }]
    let html = headerRow() + "<tbody>";
    for (const r of rows) {
      html +=
        '<tr data-table="' + r.table + '" data-key="' + esc(r.key) + '" data-seg="' +
        r.segIndex + '">' +
        '<td class="sm-key">' + esc(r.keyLabel) + "</td>" +
        langCell(r.entry, "en") +
        langCell(r.entry, "id") +
        "</tr>";
    }
    html += "</tbody>";
    tableEl.innerHTML = html;
  }

  function entriesForTable(table) {
    if (table === "initials") {
      return SM.INITIAL_KEYS.filter((k) => SM.INITIALS[k]).map((k) => ({
        table, key: k, segIndex: -1, entry: SM.INITIALS[k], keyLabel: k,
      }));
    }
    if (table === "finals") {
      return Object.keys(SM.FINALS).map((k) => ({
        table, key: k, segIndex: -1, entry: SM.FINALS[k],
        keyLabel: k === "_buzz" ? "-i (buzz)" : k,
      }));
    }
    if (table === "syllables") {
      const out = [];
      for (const k of Object.keys(SM.SYLLABLES)) {
        SM.SYLLABLES[k].forEach((seg, i) => {
          out.push({ table, key: k, segIndex: i, entry: seg, keyLabel: seg.pin || k });
        });
      }
      return out;
    }
    return [];
  }

  function renderAll() {
    renderTable($("sm-initials"), entriesForTable("initials"));
    renderTable($("sm-finals"), entriesForTable("finals"));
    renderTable($("sm-syllables"), entriesForTable("syllables"));
    $("sm-initials-count").textContent = "(" + entriesForTable("initials").length + ")";
    $("sm-finals-count").textContent = "(" + entriesForTable("finals").length + ")";
    $("sm-syllables-count").textContent = "(" + entriesForTable("syllables").length + ")";
    renderSplits();
    applyFilter();
    renderTester();
  }

  // ── split-finals reference (read-only) ───────────────────────────────────────
  function renderSplits() {
    const t = $("sm-splits");
    let html =
      "<thead><tr><th>Rime</th><th>Glide</th><th>Base rime</th><th>Reads as</th></tr></thead><tbody>";
    for (const k of Object.keys(SM.SPLIT_FINALS)) {
      const s = SM.SPLIT_FINALS[k];
      const gm = SM.FINALS[s.glide];
      const rm = SM.FINALS[s.rime];
      const chunk = (disp, seg) =>
        '<b>' + esc(disp) + '</b> → ' + previewHTML(seg && seg.en);
      html +=
        '<tr><td class="sm-key">' + esc(k) + "</td>" +
        "<td>" + chunk(s.gDisp, gm) + "</td>" +
        "<td>" + chunk(s.rDisp, rm) + "</td>" +
        "<td>" + esc(s.gDisp) + " + " + esc(s.rDisp) + "</td></tr>";
    }
    html += "</tbody>";
    t.innerHTML = html;
  }

  // ── live tester ──────────────────────────────────────────────────────────────
  function renderTester() {
    const raw = $("sm-try").value.trim();
    const out = $("sm-try-out");
    if (!raw) { out.innerHTML = ""; return; }
    const tokens = raw.split(/\s+/);
    const rows = [];
    for (const tok of tokens) {
      const r = SM.pinyinToSoundMap(tok);
      if (r) rows.push(...r);
    }
    if (!rows.length) {
      out.innerHTML = '<p class="muted sm-try-empty">No sound map for “' + esc(raw) + "”.</p>";
      return;
    }
    const col = (lang, label) => {
      let h = '<div class="sm-try-lang"><h3>' + label + "</h3>";
      let any = false;
      rows.forEach((row, i) => {
        const m = row[lang] || row.en;
        if (!m) return;
        any = true;
        const c = "seg" + (i % 3);
        h +=
          '<div class="sm-try-row">' +
          '<span class="sm-try-pin ' + c + '">' + esc(row.pin) + "</span>" +
          '<span class="sm-try-arrow">→</span>' +
          '<span class="sm-try-word">' + esc(m.pre) +
          '<span class="hit ' + c + '">' + esc(m.hit) + "</span>" +
          esc(m.post) + "</span></div>";
      });
      if (!any) h += '<p class="sm-try-empty">— none —</p>';
      return h + "</div>";
    };
    out.innerHTML = col("en", "English") + col("id", "Bahasa");
  }

  // ── edit handling ────────────────────────────────────────────────────────────
  function onEdit(e) {
    const input = e.target;
    if (input.tagName !== "INPUT" || !input.dataset.field) return;
    const tr = input.closest("tr");
    if (!tr) return;
    const { table, key, seg } = tr.dataset;
    const lang = input.dataset.lang;

    // The two inputs (word + hit) for this language cell live in this row.
    const wordEl = tr.querySelector('input[data-lang="' + lang + '"][data-field="word"]');
    const hitEl = tr.querySelector('input[data-lang="' + lang + '"][data-field="hit"]');
    const { seg: built, badHit } = buildSeg(wordEl.value, hitEl.value);

    // English is required (the card always needs an `en`); refuse to blank it.
    if (lang === "en" && !built) {
      wordEl.classList.add("sm-badhit");
      return;
    }

    // Resolve the live entry object and write the seg in place.
    let entry;
    if (table === "syllables") entry = SM.SYLLABLES[key][Number(seg)];
    else if (table === "initials") entry = SM.INITIALS[key];
    else entry = SM.FINALS[key];
    if (!entry) return;
    entry[lang] = built || undefined;

    // Feedback: dirty highlight, bad-hit flag, live preview + tester.
    [wordEl, hitEl].forEach((el) => el.classList.add("sm-dirty"));
    hitEl.classList.toggle("sm-badhit", badHit);
    wordEl.classList.remove("sm-badhit");
    const pv = tr.querySelector('.sm-pv[data-lang="' + lang + '"]');
    if (pv) pv.innerHTML = previewHTML(built);

    renderTester();
    save();
  }

  // ── filter ────────────────────────────────────────────────────────────────────
  function applyFilter() {
    const q = $("sm-filter").value.trim().toLowerCase();
    document.querySelectorAll(".sm-table tbody tr").forEach((tr) => {
      if (!tr.dataset.key) return;
      const k = (tr.dataset.key || "").toLowerCase();
      tr.style.display = !q || k.includes(q) ? "" : "none";
    });
  }

  // ── export ────────────────────────────────────────────────────────────────────
  function segLiteral(seg) {
    if (!seg) return null;
    return (
      "{ word: " + JSON.stringify(seg.word) +
      ", pre: " + JSON.stringify(seg.pre) +
      ", hit: " + JSON.stringify(seg.hit) +
      ", post: " + JSON.stringify(seg.post) + " }"
    );
  }
  function entryLiteral(entry) {
    const parts = [];
    if (entry.en) parts.push("en: " + segLiteral(entry.en));
    if (entry.id) parts.push("id: " + segLiteral(entry.id));
    return "{ " + parts.join(", ") + " }";
  }
  function jsKey(k) {
    return /^[a-z_][a-z0-9_]*$/i.test(k) ? k : JSON.stringify(k);
  }
  function buildJS() {
    const lines = [];
    lines.push("const INITIALS = {");
    for (const k of SM.INITIAL_KEYS) {
      if (SM.INITIALS[k]) lines.push("  " + jsKey(k) + ": " + entryLiteral(SM.INITIALS[k]) + ",");
    }
    lines.push("};", "", "const FINALS = {");
    for (const k of Object.keys(SM.FINALS)) {
      lines.push("  " + jsKey(k) + ": " + entryLiteral(SM.FINALS[k]) + ",");
    }
    lines.push("};", "", "const SYLLABLES = {");
    for (const k of Object.keys(SM.SYLLABLES)) {
      const segs = SM.SYLLABLES[k]
        .map((s) => {
          const parts = ["pin: " + JSON.stringify(s.pin)];
          if (s.en) parts.push("en: " + segLiteral(s.en));
          if (s.id) parts.push("id: " + segLiteral(s.id));
          return "{ " + parts.join(", ") + " }";
        })
        .join(", ");
      lines.push("  " + jsKey(k) + ": [" + segs + "],");
    }
    lines.push("};");
    return lines.join("\n");
  }

  function copyJS() {
    const text = buildJS();
    navigator.clipboard.writeText(text).then(
      () => flashSaved("Copied JS for INITIALS / FINALS / SYLLABLES"),
      () => {
        // Fallback: dump into a prompt for manual copy.
        window.prompt("Copy the sound-map JS:", text);
      }
    );
  }

  function downloadJSON() {
    const data = {
      INITIALS: SM.INITIALS,
      FINALS: SM.FINALS,
      SYLLABLES: SM.SYLLABLES,
      SPLIT_FINALS: SM.SPLIT_FINALS,
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "pinyin_sound_map.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function resetDefaults() {
    if (!window.confirm("Discard all edits and restore the default sound map?")) return;
    applySnapshot(clone(DEFAULTS));
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* ignore */ }
    renderAll();
    flashSaved("Restored defaults");
  }

  // ── wire up ───────────────────────────────────────────────────────────────────
  function init() {
    loadSaved();
    renderAll();
    document.querySelectorAll(".sm-table").forEach((t) => t.addEventListener("input", onEdit));
    $("sm-try").addEventListener("input", renderTester);
    $("sm-filter").addEventListener("input", applyFilter);
    $("sm-copy").addEventListener("click", copyJS);
    $("sm-download").addEventListener("click", downloadJSON);
    $("sm-reset").addEventListener("click", resetDefaults);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
