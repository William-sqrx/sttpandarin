// soundmap.data.js
//
// A VERBATIM browser port of the app's pinyin → familiar-word "sound map"
// (chinesely-frontend/src/lib/pinyinSoundMap.js). The tables + decomposition
// algorithm are identical, so the editor's live preview matches exactly what
// the in-app pronunciation coach card renders.
//
// Each sound-alike entry is { word, pre, hit, post } where word === pre+hit+post
// and `hit` is the slice that's the matching sound (underlined on the card).
// The editor works on { word, hit } and recomputes pre/post = split word at hit.
//
// Exposed as window.SoundMap = { INITIALS, FINALS, SYLLABLES, SPLIT_FINALS,
//   pinyinToSoundMap, normalizePinyin, splitInitialFinal, resolveFinalKey }.
// The tables are mutated in place by the editor, and pinyinToSoundMap() reads
// them at call time, so edits are reflected live.

window.SoundMap = (function () {
  // Tone-marked vowel → [base letter, tone number].
  const TONE_VOWELS = {
    "ā": ["a", 1], "á": ["a", 2], "ǎ": ["a", 3], "à": ["a", 4],
    "ē": ["e", 1], "é": ["e", 2], "ě": ["e", 3], "è": ["e", 4],
    "ī": ["i", 1], "í": ["i", 2], "ǐ": ["i", 3], "ì": ["i", 4],
    "ō": ["o", 1], "ó": ["o", 2], "ǒ": ["o", 3], "ò": ["o", 4],
    "ū": ["u", 1], "ú": ["u", 2], "ǔ": ["u", 3], "ù": ["u", 4],
    "ǖ": ["ü", 1], "ǘ": ["ü", 2], "ǚ": ["ü", 3], "ǜ": ["ü", 4],
    "ü": ["ü", 0],
  };

  // Plain-letter → that letter carrying each tone mark (index = tone, 0 = none).
  const VOWEL_MARKS = {
    a: ["a", "ā", "á", "ǎ", "à"],
    e: ["e", "ē", "é", "ě", "è"],
    i: ["i", "ī", "í", "ǐ", "ì"],
    o: ["o", "ō", "ó", "ǒ", "ò"],
    u: ["u", "ū", "ú", "ǔ", "ù"],
    "ü": ["ü", "ǖ", "ǘ", "ǚ", "ǜ"],
  };

  // Longest-first so "zh"/"ch"/"sh" win over "z"/"c"/"s".
  const INITIAL_KEYS = [
    "zh", "ch", "sh",
    "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h",
    "j", "q", "x", "r", "z", "c", "s", "y", "w",
  ];

  const RETROFLEX_BUZZ = ["zh", "ch", "sh", "r", "z", "c", "s"];

  function normalizePinyin(raw) {
    let s = String(raw || "").trim().toLowerCase();
    if (!s) return null;
    s = s.split(/\s+/)[0]; // one syllable only
    let tone = 0;
    let plain = "";
    for (const ch of s) {
      if (TONE_VOWELS[ch]) {
        const [base, t] = TONE_VOWELS[ch];
        plain += base;
        if (t) tone = t;
      } else if (ch >= "1" && ch <= "5") {
        tone = ch === "5" ? 0 : Number(ch);
      } else {
        plain += ch;
      }
    }
    plain = plain.replace(/u:/g, "ü").replace(/v/g, "ü");
    if (!plain) return null;
    return { plain, tone };
  }

  function applyToneMark(syl, tone) {
    if (!tone) return syl;
    let target = "";
    let idx = -1;
    if (syl.includes("a")) { target = "a"; idx = syl.indexOf("a"); }
    else if (syl.includes("e")) { target = "e"; idx = syl.indexOf("e"); }
    else if (syl.includes("ou")) { target = "o"; idx = syl.indexOf("o"); }
    else {
      const vowels = syl.match(/[aeiouü]/g);
      if (vowels) {
        target = vowels[vowels.length - 1];
        idx = syl.lastIndexOf(target);
      }
    }
    const marks = VOWEL_MARKS[target];
    if (idx < 0 || !marks) return syl;
    return syl.slice(0, idx) + marks[tone] + syl.slice(idx + 1);
  }

  function splitInitialFinal(plain) {
    for (const k of INITIAL_KEYS) {
      if (plain.startsWith(k)) return [k, plain.slice(k.length)];
    }
    return ["", plain];
  }

  // ── Sound-alike tables ─────────────────────────────────────────────────────

  const INITIALS = {
    b: { en: { word: "book", pre: "", hit: "b", post: "ook" }, id: { word: "baju", pre: "", hit: "b", post: "aju" } },
    p: { en: { word: "pop", pre: "", hit: "p", post: "op" }, id: { word: "pagi", pre: "", hit: "p", post: "agi" } },
    m: { en: { word: "mom", pre: "", hit: "m", post: "om" }, id: { word: "makan", pre: "", hit: "m", post: "akan" } },
    f: { en: { word: "fun", pre: "", hit: "f", post: "un" }, id: { word: "foto", pre: "", hit: "f", post: "oto" } },
    d: { en: { word: "dog", pre: "", hit: "d", post: "og" }, id: { word: "dua", pre: "", hit: "d", post: "ua" } },
    t: { en: { word: "top", pre: "", hit: "t", post: "op" }, id: { word: "tiga", pre: "", hit: "t", post: "iga" } },
    n: { en: { word: "no", pre: "", hit: "n", post: "o" }, id: { word: "nama", pre: "", hit: "n", post: "ama" } },
    l: { en: { word: "love", pre: "", hit: "l", post: "ove" }, id: { word: "lima", pre: "", hit: "l", post: "ima" } },
    g: { en: { word: "go", pre: "", hit: "g", post: "o" }, id: { word: "gula", pre: "", hit: "g", post: "ula" } },
    k: { en: { word: "key", pre: "", hit: "k", post: "ey" }, id: { word: "kaki", pre: "", hit: "k", post: "aki" } },
    h: { en: { word: "hat", pre: "", hit: "h", post: "at" }, id: { word: "hari", pre: "", hit: "h", post: "ari" } },
    // j/q/x — the "smiling" palatals. Approximated with English j / ch / sh.
    j: { en: { word: "jeep", pre: "", hit: "j", post: "eep" }, id: { word: "jalan", pre: "", hit: "j", post: "alan" } },
    q: { en: { word: "cheese", pre: "", hit: "ch", post: "eese" }, id: { word: "cinta", pre: "", hit: "c", post: "inta" } },
    x: { en: { word: "sheep", pre: "", hit: "sh", post: "eep" }, id: { word: "syukur", pre: "", hit: "sy", post: "ukur" } },
    // zh/ch/sh/r — the retroflex set (tongue curled back).
    zh: { en: { word: "jungle", pre: "", hit: "j", post: "ungle" }, id: { word: "jalan", pre: "", hit: "j", post: "alan" } },
    ch: { en: { word: "church", pre: "", hit: "ch", post: "urch" }, id: { word: "cinta", pre: "", hit: "c", post: "inta" } },
    sh: { en: { word: "show", pre: "", hit: "sh", post: "ow" }, id: { word: "syukur", pre: "", hit: "sy", post: "ukur" } },
    r: { en: { word: "rule", pre: "", hit: "r", post: "ule" }, id: { word: "raja", pre: "", hit: "r", post: "aja" } },
    // z/c/s — z = "ds", c = "ts" (no clean Bahasa match → en fallback).
    z: { en: { word: "kids", pre: "ki", hit: "ds", post: "" } },
    c: { en: { word: "cats", pre: "ca", hit: "ts", post: "" } },
    s: { en: { word: "sun", pre: "", hit: "s", post: "un" }, id: { word: "satu", pre: "", hit: "s", post: "atu" } },
    y: { en: { word: "yes", pre: "", hit: "y", post: "es" }, id: { word: "ya", pre: "", hit: "y", post: "a" } },
    w: { en: { word: "war", pre: "", hit: "w", post: "ar" }, id: { word: "waktu", pre: "", hit: "w", post: "aktu" } },
  };

  const FINALS = {
    // ── simple vowels ──
    a: { en: { word: "father", pre: "f", hit: "a", post: "ther" }, id: { word: "apa", pre: "", hit: "a", post: "pa" } },
    o: { en: { word: "saw", pre: "s", hit: "aw", post: "" }, id: { word: "toko", pre: "t", hit: "o", post: "ko" } },
    e: { en: { word: "her", pre: "h", hit: "er", post: "" }, id: { word: "emas", pre: "", hit: "e", post: "mas" } },
    "ê": { en: { word: "bet", pre: "b", hit: "e", post: "t" }, id: { word: "meja", pre: "m", hit: "e", post: "ja" } },
    i: { en: { word: "see", pre: "s", hit: "ee", post: "" }, id: { word: "ini", pre: "", hit: "i", post: "ni" } },
    u: { en: { word: "too", pre: "t", hit: "oo", post: "" }, id: { word: "satu", pre: "sat", hit: "u", post: "" } },
    "ü": { en: { word: "few", pre: "f", hit: "ew", post: "" } },
    er: { en: { word: "her", pre: "h", hit: "er", post: "" } },
    // ── diphthongs ──
    ai: { en: { word: "eye", pre: "", hit: "eye", post: "" }, id: { word: "pantai", pre: "pant", hit: "ai", post: "" } },
    ei: { en: { word: "day", pre: "d", hit: "ay", post: "" }, id: { word: "survei", pre: "surv", hit: "ei", post: "" } },
    ao: { en: { word: "now", pre: "n", hit: "ow", post: "" }, id: { word: "pulau", pre: "pul", hit: "au", post: "" } },
    ou: { en: { word: "low", pre: "l", hit: "ow", post: "" }, id: { word: "toko", pre: "t", hit: "o", post: "ko" } },
    // ── nasal finals ──
    an: { en: { word: "swan", pre: "sw", hit: "an", post: "" }, id: { word: "makan", pre: "mak", hit: "an", post: "" } },
    en: { en: { word: "sun", pre: "s", hit: "un", post: "" }, id: { word: "enam", pre: "", hit: "en", post: "am" } },
    ang: { en: { word: "sang", pre: "s", hit: "ang", post: "" }, id: { word: "pulang", pre: "pul", hit: "ang", post: "" } },
    eng: { en: { word: "sung", pre: "s", hit: "ung", post: "" }, id: { word: "lengkap", pre: "l", hit: "eng", post: "kap" } },
    ong: { en: { word: "long", pre: "l", hit: "ong", post: "" }, id: { word: "tolong", pre: "tol", hit: "ong", post: "" } },
    // ── i- glides ──
    ia: { en: { word: "yard", pre: "", hit: "ya", post: "rd" }, id: { word: "saya", pre: "sa", hit: "ya", post: "" } },
    io: { en: { word: "yawn", pre: "", hit: "yaw", post: "n" }, id: { word: "biola", pre: "b", hit: "io", post: "la" } },
    iao: { en: { word: "meow", pre: "m", hit: "eow", post: "" }, id: { word: "miau", pre: "m", hit: "iau", post: "" } },
    iu: { en: { word: "yoke", pre: "", hit: "yo", post: "ke" }, id: { word: "cium", pre: "c", hit: "iu", post: "m" } },
    ian: { en: { word: "yen", pre: "", hit: "yen", post: "" }, id: { word: "kemudian", pre: "kemud", hit: "ian", post: "" } },
    in: { en: { word: "seen", pre: "s", hit: "een", post: "" }, id: { word: "angin", pre: "ang", hit: "in", post: "" } },
    iang: { en: { word: "yang", pre: "", hit: "yang", post: "" }, id: { word: "yang", pre: "", hit: "yang", post: "" } },
    ing: { en: { word: "sing", pre: "s", hit: "ing", post: "" }, id: { word: "kucing", pre: "kuc", hit: "ing", post: "" } },
    // ── u- glides ──
    ua: { en: { word: "guava", pre: "g", hit: "ua", post: "va" }, id: { word: "dua", pre: "d", hit: "ua", post: "" } },
    uo: { en: { word: "whoa", pre: "wh", hit: "oa", post: "" }, id: { word: "kuota", pre: "k", hit: "uo", post: "ta" } },
    uai: { en: { word: "why", pre: "", hit: "why", post: "" }, id: { word: "tuai", pre: "t", hit: "uai", post: "" } },
    ui: { en: { word: "way", pre: "", hit: "way", post: "" } },
    uan: { en: { word: "wand", pre: "", hit: "wan", post: "d" }, id: { word: "uang", pre: "", hit: "uan", post: "g" } },
    un: { en: { word: "won", pre: "", hit: "won", post: "" }, id: { word: "untuk", pre: "", hit: "un", post: "tuk" } },
    uang: { en: { word: "twang", pre: "t", hit: "wang", post: "" }, id: { word: "uang", pre: "", hit: "uang", post: "" } },
    // ── ü- glides (written ue/uan/un after j/q/x/y) ──
    "üe": { en: { word: "yet", pre: "", hit: "ye", post: "t" } },
    "üan": { en: { word: "yen", pre: "", hit: "yen", post: "" } },
    "ün": { en: { word: "dune", pre: "d", hit: "une", post: "" } },
    // the buzzed -i after zh/ch/sh/r/z/c/s (no real vowel — a sustained buzz)
    _buzz: { en: { word: "sir", pre: "s", hit: "ir", post: "" } },
  };

  function resolveFinalKey(initial, final) {
    if (final === "i" && RETROFLEX_BUZZ.includes(initial)) return "_buzz";
    if (initial === "y") {
      const m = {
        a: "ia", o: "io", e: "ie", ao: "iao", ou: "iu",
        an: "ian", ang: "iang", ong: "iong",
        u: "ü", ue: "üe", uan: "üan", un: "ün",
      };
      return m[final] || final;
    }
    if (initial === "w") {
      const m = {
        a: "ua", o: "uo", ai: "uai", ei: "ui",
        an: "uan", en: "un", ang: "uang", eng: "ueng",
      };
      return m[final] || final;
    }
    if (initial === "j" || initial === "q" || initial === "x") {
      const m = { u: "ü", ue: "üe", uan: "üan", un: "ün" };
      return m[final] || final;
    }
    return final;
  }

  // Finals taught as TWO sound-alikes (glide + base rime). glide/rime are FINALS
  // keys; gDisp/rDisp are what to show as the pinyin chunk.
  const SPLIT_FINALS = {
    iong: { glide: "i", rime: "ong", gDisp: "i", rDisp: "ong" },
    ie: { glide: "i", rime: "ê", gDisp: "i", rDisp: "e" },
    ueng: { glide: "u", rime: "eng", gDisp: "u", rDisp: "eng" },
  };

  // Whole-syllable overrides — the buzzed -i syllables with no separable vowel.
  const SYLLABLES = {
    zi: [{ pin: "zi", en: { word: "kids", pre: "ki", hit: "ds", post: "" }, id: { word: "cermin", pre: "", hit: "ce", post: "rmin" } }],
    ci: [{ pin: "ci", en: { word: "cats", pre: "ca", hit: "ts", post: "" }, id: { word: "cermin", pre: "", hit: "ce", post: "rmin" } }],
    si: [{ pin: "si", en: { word: "sir", pre: "s", hit: "ir", post: "" }, id: { word: "sebab", pre: "", hit: "se", post: "bab" } }],
    zhi: [{ pin: "zhi", en: { word: "jeer", pre: "", hit: "jeer", post: "" }, id: { word: "cetak", pre: "", hit: "ce", post: "tak" } }],
    chi: [{ pin: "chi", en: { word: "cheer", pre: "", hit: "cheer", post: "" }, id: { word: "cetak", pre: "", hit: "ce", post: "tak" } }],
    shi: [{ pin: "shi", en: { word: "sure", pre: "", hit: "sure", post: "" }, id: { word: "sebab", pre: "", hit: "se", post: "bab" } }],
    ri: [{ pin: "ri", en: { word: "azure", pre: "a", hit: "z", post: "ure" } }],
  };

  // Map a single pinyin syllable → sound-map segments (identical to the app).
  function pinyinToSoundMap(raw) {
    const norm = normalizePinyin(raw);
    if (!norm) return null;
    const { plain, tone } = norm;

    const override = SYLLABLES[plain];
    if (override) {
      return override.map((seg) => ({
        pin: applyToneMark(seg.pin, tone),
        en: seg.en,
        id: seg.id,
      }));
    }

    const [initial, final] = splitInitialFinal(plain);

    const rows = [];
    if (initial && INITIALS[initial]) {
      rows.push({ pin: initial, en: INITIALS[initial].en, id: INITIALS[initial].id });
    }

    if (final) {
      const key = resolveFinalKey(initial, final);
      const split = SPLIT_FINALS[key];
      if (split) {
        const skipGlide =
          (initial === "y" && split.glide === "i") ||
          (initial === "w" && split.glide === "u");
        if (!skipGlide) {
          const gm = FINALS[split.glide];
          rows.push({ pin: split.gDisp, en: gm.en, id: gm.id });
        }
        const rm = FINALS[split.rime];
        rows.push({ pin: applyToneMark(split.rDisp, tone), en: rm.en, id: rm.id });
      } else {
        let fm = FINALS[key] || FINALS[final];
        if (!fm) {
          const core = (key.match(/[aeiouü]/g) || []).pop();
          fm = core ? FINALS[core] : null;
        }
        if (fm) rows.push({ pin: applyToneMark(final, tone), en: fm.en, id: fm.id });
      }
    }

    return rows.length ? rows : null;
  }

  return {
    TONE_VOWELS, VOWEL_MARKS, INITIAL_KEYS, RETROFLEX_BUZZ,
    INITIALS, FINALS, SPLIT_FINALS, SYLLABLES,
    normalizePinyin, applyToneMark, splitInitialFinal, resolveFinalKey,
    pinyinToSoundMap,
  };
})();
