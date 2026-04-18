(async function () {
  // Tab switching
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach(t => t.addEventListener("click", () => {
    tabs.forEach(x => x.classList.toggle("active", x === t));
    document.getElementById("panel-words").hidden = t.dataset.tab !== "words";
    document.getElementById("panel-exam").hidden = t.dataset.tab !== "exam";
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
  fill("e-male", defaults.male_voices, "Cherry");

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
})();
