(async function () {
  // Tab switching
  const HSK_PASSWORD  = "michelle";
  const FISH_PASSWORD = "william";
  const tabs = document.querySelectorAll(".tab");
  const panels = ["words", "exam", "hsk", "fish"];

  tabs.forEach(t => t.addEventListener("click", () => {
    if (t.dataset.tab === "hsk" && sessionStorage.getItem("hskUnlocked") !== "1") {
      const entered = window.prompt("Enter password for HSK Browser:");
      if (entered !== HSK_PASSWORD) { if (entered !== null) window.alert("Wrong password."); return; }
      sessionStorage.setItem("hskUnlocked", "1");
    }
    if (t.dataset.tab === "fish" && sessionStorage.getItem("fishUnlocked") !== "1") {
      const entered = window.prompt("Enter password for Fish Studio:");
      if (entered !== FISH_PASSWORD) { if (entered !== null) window.alert("Wrong password."); return; }
      sessionStorage.setItem("fishUnlocked", "1");
    }
    if (t.dataset.tab === "fish") fsInit();
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

  // ===== Fish Studio — pixel-art ocean canvas =====
  const _SKY_RATIO    = 0.33;
  const _SAND_H_RATIO = 0.2;
  const _SEA_PX       = 2;
  const _SEA_BAND     = 24;
  const _SEA_FLAT_TOP = -6;
  const _BAYER = [[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]];
  const _CAUSTIC_DEFS = [
    { xRatio:0.12, width:28, opacity:0.18, speed:0.00045, drift:28, phase:0.0  },
    { xRatio:0.28, width:18, opacity:0.14, speed:0.00032, drift:20, phase:1.2  },
    { xRatio:0.45, width:35, opacity:0.22, speed:0.00038, drift:24, phase:2.5  },
    { xRatio:0.62, width:20, opacity:0.16, speed:0.0005,  drift:18, phase:0.7  },
    { xRatio:0.80, width:26, opacity:0.19, speed:0.00028, drift:22, phase:3.8  },
  ];
  const _SKY_PAL = [
    { hour:0,    top:"#050820", bot:"#1a1648" },
    { hour:5,    top:"#120f38", bot:"#3a1e5c" },
    { hour:6,    top:"#ff7b4a", bot:"#ffcc80" },
    { hour:8,    top:"#5aa2d8", bot:"#cbe4f0" },
    { hour:12,   top:"#3ba8e6", bot:"#ffe27a" },
    { hour:15,   top:"#2a70c0", bot:"#9bc9de" },
    { hour:17.5, top:"#e8803a", bot:"#f5c842" },
    { hour:19,   top:"#6b2d6b", bot:"#c05050" },
    { hour:20.5, top:"#0e1138", bot:"#2a2060" },
    { hour:24,   top:"#050820", bot:"#1a1648" },
  ];
  const _TIME_PAL = [
    { hour:0,    water:["#2e3a88","#202a72","#152058","#0d1d40","#0a2638","#04111e"], sand:["#6a4e20","#4e3812","#7a5e2e","#6a4c1c"] },
    { hour:5,    water:["#3a2078","#342070","#2e1c68","#281858","#201448","#1a1038"], sand:["#7a5a28","#5a4018","#8a6a38","#7a5820"] },
    { hour:6.5,  water:["#e08868","#cc7858","#b86848","#a45838","#905028","#7c4818"], sand:["#c8924a","#a07232","#daa45a","#c88e3a"] },
    { hour:8,    water:["#b8ecdc","#78d4d0","#48b4c8","#2495c0","#1678ae","#0a5c8c"], sand:["#c8973a","#b07828","#e0b458","#d0a040"] },
    { hour:12,   water:["#e4f6c0","#8ee0c4","#3cc2cc","#1a98c4","#0d70a8","#054c7e"], sand:["#d4a144","#b88230","#e8be64","#d8aa48"] },
    { hour:15,   water:["#9ce6d4","#5fc0c8","#3a9abd","#1e76ae","#14588c","#0a3f6a"], sand:["#c8973a","#b07828","#e0b458","#d0a040"] },
    { hour:17.5, water:["#e09060","#c87848","#b06050","#985060","#804870","#684060"], sand:["#cc9448","#a47030","#e0a85a","#cc9038"] },
    { hour:19.5, water:["#5840a8","#4c3898","#403088","#342868","#282050","#1c1840"], sand:["#906840","#706028","#a07848","#906030"] },
    { hour:21,   water:["#1e2a68","#19235a","#141c4c","#10173e","#0d1338","#0a1030"], sand:["#7a6030","#5a4820","#8a7040","#7a5828"] },
    { hour:24,   water:["#2e3a88","#202a72","#152058","#0d1d40","#0a2638","#04111e"], sand:["#6a4e20","#4e3812","#7a5e2e","#6a4c1c"] },
  ];

  function _hr2rgb(hex) {
    if (hex.startsWith("rgb")) { const m=hex.match(/(\d+),\s*(\d+),\s*(\d+)/); return [+m[1],+m[2],+m[3]]; }
    const h=hex.replace("#","");
    return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)];
  }
  function _lhex(a,b,t) {
    const [r1,g1,b1]=_hr2rgb(a),[r2,g2,b2]=_hr2rgb(b);
    return `rgb(${Math.round(r1+(r2-r1)*t)},${Math.round(g1+(g2-g1)*t)},${Math.round(b1+(b2-b1)*t)})`;
  }
  function _lightenC(c,a) { const [r,g,b]=_hr2rgb(c); return `rgb(${Math.min(255,r+a)},${Math.min(255,g+a)},${Math.min(255,b+a)})`; }
  function _darkenC(c,a)  { const [r,g,b]=_hr2rgb(c); return `rgb(${Math.max(0,r-a)},${Math.max(0,g-a)},${Math.max(0,b-a)})`; }

  function _ocHour() { const n=new Date(); return n.getHours()+n.getMinutes()/60; }
  function _ocSky(hour) {
    let p=_SKY_PAL[0], n=_SKY_PAL[_SKY_PAL.length-1];
    for(let i=0;i<_SKY_PAL.length-1;i++){if(hour>=_SKY_PAL[i].hour&&hour<_SKY_PAL[i+1].hour){p=_SKY_PAL[i];n=_SKY_PAL[i+1];break;}}
    const range=n.hour-p.hour, t=range===0?0:(hour-p.hour)/range;
    return { top:_lhex(p.top,n.top,t), bot:_lhex(p.bot,n.bot,t) };
  }
  function _ocTime(hour) {
    let p=_TIME_PAL[0], n=_TIME_PAL[_TIME_PAL.length-1];
    for(let i=0;i<_TIME_PAL.length-1;i++){if(hour>=_TIME_PAL[i].hour&&hour<_TIME_PAL[i+1].hour){p=_TIME_PAL[i];n=_TIME_PAL[i+1];break;}}
    const range=n.hour-p.hour, t=range===0?0:(hour-p.hour)/range;
    return { water:p.water.map((c,i)=>_lhex(c,n.water[i],t)), sand:p.sand.map((c,i)=>_lhex(c,n.sand[i],t)) };
  }
  function _buildPalette(water) {
    const pal=[];
    for(let i=0;i<_SEA_BAND;i++){
      const t=i/(_SEA_BAND-1), segs=water.length-1;
      const seg=Math.min(segs-1,Math.floor(t*segs)), lt=t*segs-seg;
      const base=_lhex(water[seg],water[seg+1],lt);
      pal.push(_lhex(base,"#001030",Math.min(0.35,t*0.4)));
    }
    return pal;
  }
  function _drawDither(ctx, W, seaH, pal, offY) {
    const cols=Math.ceil(W/_SEA_PX), rows=Math.ceil(seaH/_SEA_PX);
    for(let ry=0;ry<rows;ry++){
      const y=ry*_SEA_PX;
      const dt=Math.max(0,(y-_SEA_FLAT_TOP)/Math.max(1,seaH-_SEA_FLAT_TOP));
      const bp=dt*(_SEA_BAND-1);
      const bLo=Math.floor(bp), bHi=Math.min(_SEA_BAND-1,bLo+1), frac=bp-bLo;
      const brow=_BAYER[ry&3];
      const hi=[frac>(brow[0]+.5)/16, frac>(brow[1]+.5)/16, frac>(brow[2]+.5)/16, frac>(brow[3]+.5)/16];
      const hc=hi.filter(Boolean).length;
      if(hc===0){
        ctx.fillStyle=pal[bLo]; ctx.fillRect(0,offY+y,W,_SEA_PX);
      } else if(hc===4){
        ctx.fillStyle=pal[bHi]; ctx.fillRect(0,offY+y,W,_SEA_PX);
      } else {
        ctx.fillStyle=pal[bLo]; ctx.fillRect(0,offY+y,W,_SEA_PX);
        ctx.fillStyle=pal[bHi];
        for(let c=0;c<4;c++){ if(!hi[c])continue; for(let cx=c;cx<cols;cx+=4) ctx.fillRect(cx*_SEA_PX,offY+y,_SEA_PX,_SEA_PX); }
      }
    }
  }
  function _drawSandLayer(ctx, W, seaH, sandY, offY, sand) {
    ctx.fillStyle=sand[0]; ctx.fillRect(0,offY+sandY,W,seaH-sandY);
    ctx.fillStyle=sand[1]; ctx.fillRect(0,offY+sandY+8,W,seaH-sandY-8);
    ctx.fillStyle=sand[2]; ctx.fillRect(0,offY+sandY,W,3);
    ctx.fillStyle=sand[3]; ctx.fillRect(0,offY+sandY+3,W,2);
    const sandH=seaH-sandY;
    ctx.strokeStyle=_lightenC(sand[0],10); ctx.lineWidth=1;
    ctx.beginPath();
    for(let r=0;r<4;r++){
      const ry=offY+sandY+14+r*(sandH/5);
      ctx.moveTo(0,ry);
      for(let x=8;x<=W;x+=8) ctx.lineTo(x,ry+Math.sin(x*0.04+r*1.5)*1.5);
    }
    ctx.stroke();
    for(let i=0;i<50;i++){
      const h1=((i*374761393+7)>>>0)%65536, h2=((i*668265263+13)>>>0)%65536;
      ctx.fillStyle=i%3===0?_lightenC(sand[0],14):_darkenC(sand[1],12);
      ctx.fillRect(Math.floor((h1/65535)*W), Math.floor(offY+sandY+6+(h2/65535)*(sandH-10)), 2, 2);
    }
  }

  let _ocBg=null, _ocBgMin=-1;
  function _buildOcBg(W, H) {
    const bg=document.createElement("canvas"); bg.width=W; bg.height=H;
    const ctx=bg.getContext("2d");
    const hour=_ocHour(), sky=_ocSky(hour), tc=_ocTime(hour);
    const skyH=Math.floor(H*_SKY_RATIO), seaH=H-skyH, sandY=Math.floor(seaH*(1-_SAND_H_RATIO));
    // Sky
    const grd=ctx.createLinearGradient(0,0,0,skyH);
    grd.addColorStop(0,sky.top); grd.addColorStop(1,sky.bot);
    ctx.fillStyle=grd; ctx.fillRect(0,0,W,skyH);
    // Dithered water
    _drawDither(ctx, W, seaH, _buildPalette(tc.water), skyH);
    // Edge vignettes
    let gL=ctx.createLinearGradient(0,skyH,W*0.25,skyH);
    gL.addColorStop(0,"rgba(0,10,30,0.45)"); gL.addColorStop(1,"rgba(0,10,30,0)");
    ctx.fillStyle=gL; ctx.fillRect(0,skyH,W*0.25,seaH);
    let gR=ctx.createLinearGradient(W*0.75,skyH,W,skyH);
    gR.addColorStop(0,"rgba(0,10,30,0)"); gR.addColorStop(1,"rgba(0,10,30,0.45)");
    ctx.fillStyle=gR; ctx.fillRect(W*0.75,skyH,W*0.25,seaH);
    // Pixel-art mist streaks
    const streaks=[{y:0.18,x:0.15,w:0.35,op:0.12},{y:0.22,x:0.55,w:0.28,op:0.10},
                   {y:0.34,x:0.08,w:0.22,op:0.09},{y:0.38,x:0.62,w:0.32,op:0.11},
                   {y:0.48,x:0.20,w:0.45,op:0.08}];
    for(const s of streaks){
      ctx.fillStyle=`rgba(220,235,255,${s.op})`;
      ctx.fillRect(W*s.x, skyH+Math.floor(seaH*s.y), W*s.w, 2);
      ctx.fillRect(W*(s.x+0.02), skyH+Math.floor(seaH*s.y)+3, W*(s.w-0.05), 1);
    }
    // Sand
    _drawSandLayer(ctx, W, seaH, sandY, skyH, tc.sand);
    _ocBg=bg;
  }
  function _drawCausticLayer(ctx, W, seaH, skyH, ts) {
    for(const c of _CAUSTIC_DEFS){
      const x=c.xRatio*W+c.drift*Math.sin(c.speed*ts+c.phase);
      const steps=Math.floor(seaH*0.55/6);
      for(let i=0;i<steps;i++){
        const taper=1-i/steps, w=Math.max(2,Math.round(c.width*taper));
        ctx.fillStyle=`rgba(255,255,255,${c.opacity})`;
        ctx.fillRect(Math.round(x-w/2), skyH+i*6, w, 4);
      }
    }
  }
  function fsStartOcean() {
    const canvas=document.getElementById("fs-canvas"); if(!canvas) return;
    const resize=()=>{
      const r=canvas.getBoundingClientRect();
      const W=Math.round(r.width)||900, H=Math.round(r.height)||500;
      if(W!==canvas.width||H!==canvas.height){ canvas.width=W; canvas.height=H; _ocBg=null; }
    };
    new ResizeObserver(resize).observe(canvas); resize();
    function frame(ts){
      requestAnimationFrame(frame);
      const W=canvas.width, H=canvas.height; if(!W||!H) return;
      const min=Math.floor(_ocHour()*60);
      if(!_ocBg||_ocBg.width!==W||_ocBg.height!==H||min!==_ocBgMin){ _buildOcBg(W,H); _ocBgMin=min; }
      const ctx=canvas.getContext("2d");
      ctx.drawImage(_ocBg,0,0);
      _drawCausticLayer(ctx, W, H-Math.floor(H*_SKY_RATIO), Math.floor(H*_SKY_RATIO), ts);
    }
    requestAnimationFrame(frame);
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

  function fsInit() {
    if (fsLoaded) return;
    fsLoaded = true;
    fsStartOcean();
    fsLoadExisting();
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

    let frame = 0;
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
      frame = (frame + 1) % total;
    }
    requestAnimationFrame(draw);
  }

  // Download selected sprite sheet (1024×1024 for OpenAI compatibility)
  document.getElementById("fs-dl-btn").addEventListener("click", async () => {
    if (!fsSelectedId) return;
    const url = "/api/sprite/" + fsSelectedId + "/image?size=1024&t=" + Date.now();
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
})();
