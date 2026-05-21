"""Fish animations gallery. Generation runs in a background thread on
the prod server (kicked off by trigger_batch.py). Each fish reference
image is animated into looping MP4 clips via Veo 3.1, then each clip is
cropped and packed into a 5x5 / 24-frame sprite sheet (see veo_gen.py).
The sprite sheets are stored in MongoDB so they survive Render redeploys
+ dyno restarts.

A keepalive pinger fires the public URL every minute while the batch
runs, so the free-tier dyno doesn't spin down mid-job.

All routes are unauthenticated — anyone with the URL can view, trigger,
or stop the batch.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bson import Binary
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from db import (
    fish_anims_col,
    fish_anims_refs_col,
    fish_anims_settings_col,
    fish_anims_skips_col,
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
INPUT_DIR = APP_DIR / "Chinesely Fish (256)"
PER_FISH = 4  # Veo emits this many clip variants per call (one call per fish)
KEEPALIVE_SECS = 60
MAX_CONSECUTIVE_FAILS = 3  # auto-stop after this many back-to-back fish failures
MAX_REF_BYTES = 8 * 1024 * 1024  # 8 MB ceiling on uploaded reference images
MAX_SHEET_BYTES = 15 * 1024 * 1024  # stay under MongoDB's 16 MB document limit
ALLOWED_REF_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
_VEO_PROMPT_KEY = "veo_prompt"       # settings-collection key for the editable prompt
MAX_PROMPT_CHARS = 12000             # ceiling on a user-supplied Veo prompt

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# Only these species are surfaced in the gallery / batch — and only their
# baby + teen stages (no adult). Disk PNGs or MongoDB sheets outside this
# set are filtered out everywhere (list endpoint + batch loop).
_ALLOWED_SPECIES = (
    "angelfish", "axolotl", "boxfish", "clownfish", "coralbeauty",
    "dolphin", "hammerhead", "harlequin", "jawfish", "jellyfish",
    "koi", "lionfish", "mandarin", "mantaray", "octopus",
    "pufferfish", "rasbora", "royalgramma", "seadragon", "seahorse",
    "shark", "sunfish", "trout", "whale", "yellowtang", "zebrafish",
)
ALLOWED_STEMS = frozenset(
    f"{stage}{sp}" for sp in _ALLOWED_SPECIES for stage in ("baby", "teen")
)


def _safe_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise HTTPException(400, "bad name")
    return name


def _get_ref_bytes(stem: str, png_path: Path | None) -> bytes:
    """Return the reference image bytes for `stem`. Prefers a user-uploaded
    ref from MongoDB; falls back to the on-disk default in INPUT_DIR.
    Raises FileNotFoundError if neither source has bytes for this stem."""
    try:
        doc = fish_anims_refs_col().find_one({"name": stem}, {"ref": 1})
        if doc and doc.get("ref"):
            return bytes(doc["ref"])
    except Exception as e:  # noqa: BLE001
        print(f"[fishanims] read custom ref {stem}: {e}", flush=True)
    if png_path is not None and png_path.exists():
        return png_path.read_bytes()
    raise FileNotFoundError(f"no reference image available for '{stem}'")


def _disk_stems() -> list[str]:
    """Sorted list of every species stem in INPUT_DIR. Used by the list
    endpoint so disk-only fish (no sheets generated yet, no custom ref)
    still appear with an Upload button."""
    if not INPUT_DIR.is_dir():
        return []
    return sorted(
        p.stem for p in INPUT_DIR.glob("*.png")
        if any(c.isalpha() for c in p.stem) and p.stem in ALLOWED_STEMS
    )


# ----- Batch background runner ----------------------------------------------

@dataclass
class _BatchStatus:
    state: str = "idle"   # idle | running | finished | stopped | error
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    current: str = ""
    error: str | None = None        # set when state == "error"
    last_error: str | None = None   # most recent per-sheet failure detail
    started_at: float | None = None
    finished_at: float | None = None


_status = _BatchStatus()
_lock = threading.Lock()
_stop_flag = threading.Event()
_thread: threading.Thread | None = None
_migrated_once = False
_skip_fish: set[str] = set()
_skip_set_loaded = False  # in-memory cache of persistent skips loaded yet?
_regen_queue: list[str] = []  # FIFO of fish to fully regenerate (fresh clips)
_log: "deque[str]" = deque(maxlen=200)  # recent activity lines, shown in the UI


def _log_event(msg: str) -> None:
    """Append a timestamped line to the in-memory activity log (surfaced in
    the UI via the status endpoint) and echo it to stdout for Render logs."""
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}"
    with _lock:
        _log.append(line)
    print(f"[fishanims] {msg}", flush=True)


def _ensure_skip_set_loaded() -> None:
    """Load persistent skips from MongoDB into the in-memory _skip_fish cache.
    Runs at most once per process — subsequent calls are no-ops. The skip
    endpoints keep MongoDB and the cache in sync after this initial load."""
    global _skip_set_loaded
    if _skip_set_loaded:
        return
    try:
        persisted = {d["name"]
                     for d in fish_anims_skips_col().find({}, {"name": 1})}
        with _lock:
            _skip_fish.update(persisted)
        _skip_set_loaded = True
    except Exception as e:  # noqa: BLE001
        print(f"[fishanims] load skips skipped: {e}", flush=True)


def _ensure_migrated() -> None:
    """Idempotent disk → MongoDB migration of any sheets committed to git
    before the storage switch. Runs at most once per process lifetime."""
    global _migrated_once
    if _migrated_once:
        return
    try:
        _migrate_disk_to_mongo(fish_anims_col())
    except Exception as e:  # noqa: BLE001
        print(f"[fishanims] migrate skipped: {e}", flush=True)
    _migrated_once = True


def _keepalive_loop(stop_event: threading.Event) -> None:
    """Hit the public URL every minute so Render's dyno doesn't spin down.
    Free-tier inactivity is measured by external HTTP traffic, so we go
    out → internet → load balancer → back into the container."""
    import requests

    url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if not url:
        return
    target = url.rstrip("/") + "/"
    while not stop_event.is_set():
        try:
            requests.get(target, timeout=10)
        except Exception:  # noqa: BLE001
            pass
        for _ in range(KEEPALIVE_SECS):
            if stop_event.is_set():
                return
            time.sleep(1)


def _migrate_disk_to_mongo(col) -> int:
    """One-shot import of any local fish_anims/<species>/<idx>.png+.json into
    MongoDB. Idempotent — existing docs are skipped. Returns count migrated.
    Lets us preserve sheets that were committed to git before the MongoDB
    switch so we don't pay PixelLab to regenerate them."""
    import json
    legacy = APP_DIR / "fish_anims"
    if not legacy.is_dir():
        return 0
    n = 0
    for species_dir in legacy.iterdir():
        if not species_dir.is_dir():
            continue
        for png_path in species_dir.glob("*.png"):
            if not png_path.stem.isdigit():
                continue
            idx = int(png_path.stem)
            meta_path = png_path.with_suffix(".json")
            if not meta_path.exists():
                continue
            if col.find_one({"name": species_dir.name, "idx": idx}, {"_id": 1}):
                continue
            try:
                meta = json.loads(meta_path.read_text())
                col.update_one(
                    {"name": species_dir.name, "idx": idx},
                    {"$set": {
                        "name": species_dir.name,
                        "idx": idx,
                        "sheet": Binary(png_path.read_bytes()),
                        "frames": meta.get("frames", 1),
                        "frameW": meta.get("frameW", 256),
                        "frameH": meta.get("frameH", 256),
                        "created_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
                n += 1
                print(f"[fishanims] migrated {species_dir.name}/{idx} → mongo",
                      flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[fishanims] migrate FAIL {species_dir.name}/{idx}: {e}",
                      flush=True)
    return n


def _flatten_to_black(data: bytes) -> bytes:
    """Composite a (possibly transparent) image onto a solid black
    background, returning PNG bytes. Fish references ship as transparent
    PNGs; both the web preview and the Veo input use the black-backed
    version so generated clips have a clean, easily-cropped background."""
    try:
        import io  # noqa: WPS433
        from PIL import Image  # noqa: WPS433
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        out = io.BytesIO()
        bg.save(out, format="PNG")
        return out.getvalue()
    except Exception as e:  # noqa: BLE001
        print(f"[fishanims] flatten-to-black failed: {e}", flush=True)
        return data


def _get_veo_prompt() -> str:
    """The Veo prompt to send — the user-edited override saved in MongoDB,
    or veo_gen.VEO_PROMPT if none has been saved (or the read fails)."""
    import veo_gen  # noqa: WPS433
    try:
        doc = fish_anims_settings_col().find_one(
            {"key": _VEO_PROMPT_KEY}, {"value": 1})
        if doc and isinstance(doc.get("value"), str) and doc["value"].strip():
            return doc["value"]
    except Exception as e:  # noqa: BLE001
        print(f"[fishanims] read veo prompt: {e}", flush=True)
    return veo_gen.VEO_PROMPT


def _save_sheet_doc(col, stem: str, idx: int, doc: dict, label: str) -> bool:
    """Persist one sprite-sheet doc to MongoDB with retry/backoff. Returns
    True on a confirmed write."""
    for db_attempt in range(1, 7):  # ~5s, 10s, 20s, 40s, 60s, 60s
        try:
            col.update_one(
                {"name": stem, "idx": idx},
                {"$set": doc},
                upsert=True,
            )
            if not col.find_one({"name": stem, "idx": idx}, {"_id": 1}):
                raise RuntimeError("upsert returned ok but doc not found")
            print(f"[fishanims] ok {stem}/{idx}{label} "
                  f"({len(doc['sheet'])//1024}KB sheet → mongo)", flush=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[fishanims] mongo write {db_attempt} {stem}/{idx}{label}: {e}",
                  flush=True)
            if db_attempt < 6:
                time.sleep(min(60, 5 * (2 ** (db_attempt - 1))))
    print(f"[fishanims] PERMANENT MONGO WRITE FAIL {stem}/{idx}{label} "
          f"(sheet bytes lost — re-trigger to regenerate)", flush=True)
    return False


def _generate_and_save_fish(col, stem: str, ref_bytes: bytes,
                            label: str = "") -> bool:
    """Animate one fish with Veo, then turn each clip into a 5x5 sprite
    sheet (see veo_gen.video_to_sprite_sheet) and persist it as its own
    (name, idx) doc. Any prior docs for the fish are wiped first so a
    re-run replaces rather than appends. Updates _status counters. Returns
    True if at least one sheet was saved."""
    import veo_gen  # noqa: WPS433

    # The reference is transparent; Veo needs an opaque image and the
    # crop step needs a clean background — composite onto black first.
    ref_black = _flatten_to_black(ref_bytes)
    prompt = _get_veo_prompt()

    def _on_progress(elapsed: int, done: int, total: int) -> None:
        with _lock:
            _status.current = (
                f"{stem} — Veo {done}/{total} clips ready ({elapsed}s)")

    _log_event(f"{stem}{label}: requesting {PER_FISH} clips from Veo…")
    veo_start = time.time()
    videos: list[bytes] | None = None
    last_err: Exception | None = None
    for attempt in (1, 2):
        if _stop_flag.is_set():
            break
        try:
            videos = veo_gen.generate_videos(
                ref_black, should_stop=_stop_flag.is_set, prompt=prompt,
                on_progress=_on_progress)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            # A Stop press aborts the Veo wait — don't burn a retry on it.
            if _stop_flag.is_set():
                break
            _log_event(f"{stem}{label}: Veo attempt {attempt} failed — {e}")
            if attempt < 2:
                time.sleep(10)
    if not videos:
        if _stop_flag.is_set():
            # Aborted by a Stop press — not a failure, leave counters alone.
            _log_event(f"{stem}{label}: aborted (stop requested)")
            return False
        with _lock:
            _status.failed += PER_FISH
            _status.last_error = f"{stem}{label}: {last_err}"
        _log_event(f"{stem}{label}: FAILED — {last_err}")
        return False

    _log_event(
        f"{stem}{label}: Veo returned {len(videos)} clip(s) in "
        f"{int(time.time() - veo_start)}s — building sprite sheets")

    # Replace — drop any prior docs so idx numbering stays clean (1..N).
    deleted = col.delete_many({"name": stem}).deleted_count
    if deleted:
        print(f"[fishanims] {stem}{label}: wiped {deleted} prior doc(s)",
              flush=True)

    saved = 0
    n_clips = len(videos)
    for idx, mp4 in enumerate(videos[:PER_FISH], start=1):
        if _stop_flag.is_set():
            break
        with _lock:
            _status.current = f"{stem} — building sheet {idx}/{n_clips}"
        try:
            sheet_png, cols, rows, frames, fw, fh = \
                veo_gen.video_to_sprite_sheet(mp4)
        except Exception as e:  # noqa: BLE001
            _log_event(f"{stem}/{idx}{label}: sheet build FAILED — {e}")
            with _lock:
                _status.last_error = f"{stem}/{idx}{label}: sheet build — {e}"
            continue
        if len(sheet_png) > MAX_SHEET_BYTES:
            _log_event(f"{stem}/{idx}{label}: sheet too big "
                       f"({len(sheet_png)//1024}KB) — skipped")
            continue
        doc = {
            "name": stem,
            "idx": idx,
            "sheet": Binary(sheet_png),
            "cols": cols,
            "rows": rows,
            "frames": frames,
            "frameW": fw,
            "frameH": fh,
            "created_at": datetime.now(timezone.utc),
        }
        if _save_sheet_doc(col, stem, idx, doc, label):
            saved += 1
            _log_event(f"{stem}/{idx}{label}: sheet saved "
                       f"({fw}×{fh}, {frames}f)")

    with _lock:
        _status.done += saved
        if saved < PER_FISH:
            _status.failed += (PER_FISH - saved)
            if saved == 0:
                _status.last_error = f"{stem}{label}: no clips saved"
    _log_event(f"{stem}{label}: complete — {saved}/{PER_FISH} sheets saved")
    return saved > 0


def _drain_regen_queue(col, pngs) -> None:
    """Process every pending regen request: re-run Veo for the fish (which
    wipes its existing clips and saves fresh ones). Bumps _status.total so
    the progress bar accounts for the extra work. Honors the stop flag."""
    while True:
        with _lock:
            if not _regen_queue or _stop_flag.is_set():
                return
            stem = _regen_queue.pop(0)
        # `png` may be None for a fish that only has an uploaded custom
        # ref (no disk default) — that's fine, _get_ref_bytes handles it.
        png = next((p for p in pngs if p.stem == stem), None)
        with _lock:
            _status.total += PER_FISH
            _status.current = f"{stem} (veo regen)"
        try:
            ref_bytes = _get_ref_bytes(stem, png)
        except FileNotFoundError as e:
            with _lock:
                _status.failed += PER_FISH
                _status.last_error = str(e)
            print(f"[fishanims] regen FAIL {stem}: {e}", flush=True)
            continue
        _generate_and_save_fish(col, stem, ref_bytes, label=" regen")


def _batch_loop(regen_only: bool = False) -> None:
    """Background worker. A full run (regen_only=False) sweeps every fish;
    a regen-only run skips the sweep and processes just the regen queue —
    so clicking Regen on one fish never kicks off generation for the rest."""
    keepalive_stop = threading.Event()
    keepalive_thread = threading.Thread(
        target=_keepalive_loop, args=(keepalive_stop,), daemon=True)
    try:
        col = fish_anims_col()
        _ensure_migrated()
        _ensure_skip_set_loaded()

        if not INPUT_DIR.is_dir():
            with _lock:
                _status.state = "error"
                _status.error = f"input folder not found: {INPUT_DIR}"
                _status.finished_at = time.time()
            return

        pngs = sorted(p for p in INPUT_DIR.glob("*.png")
                      if any(c.isalpha() for c in p.stem)
                      and p.stem in ALLOWED_STEMS)
        with _lock:
            # A regen-only run starts at 0 — _drain_regen_queue bumps the
            # total per queued fish.
            _status.total = 0 if regen_only else len(pngs) * PER_FISH

        keepalive_thread.start()
        consecutive_fails = 0
        if regen_only:
            _log_event("regen run started")
        else:
            _log_event(f"batch started — {len(pngs)} fish, "
                       f"{PER_FISH} sheets each")

        # regen-only: iterate nothing, fall straight through to the regen
        # drain below. A full run sweeps every fish.
        for png in ([] if regen_only else pngs):
            if _stop_flag.is_set():
                break
            stem = png.stem
            with _lock:
                skip = stem in _skip_fish
                _status.current = f"{stem} (veo)"
            if skip:
                with _lock:
                    _status.skipped += PER_FISH
                continue

            # Fish already fully generated — leave it untouched on resume.
            if col.count_documents(
                {"name": stem, "cols": {"$exists": True}}
            ) >= PER_FISH:
                with _lock:
                    _status.skipped += PER_FISH
                continue

            try:
                ref_bytes = _get_ref_bytes(stem, png)
            except FileNotFoundError as e:
                with _lock:
                    _status.failed += PER_FISH
                    _status.last_error = str(e)
                print(f"[fishanims] FAIL {stem}: {e}", flush=True)
                continue

            ok = _generate_and_save_fish(col, stem, ref_bytes)
            # A Stop press aborts the fish mid-way — that's not a real
            # failure, so don't let it trip the auto-stop counter.
            if _stop_flag.is_set():
                break
            if ok:
                consecutive_fails = 0
            else:
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    with _lock:
                        _status.state = "error"
                        _status.error = (
                            f"auto-stopped after {consecutive_fails} consecutive "
                            f"Veo failures — check Render logs for the reason"
                        )
                        _status.finished_at = time.time()
                        _status.current = ""
                    print(f"[fishanims] auto-stop: {consecutive_fails} consecutive "
                          f"fails — bailing out", flush=True)
                    return
            # After each fish, drain any regen requests that arrived while
            # Veo was running so regen takes effect promptly.
            _drain_regen_queue(col, pngs)

        # Drain the regen queue — the only work in a regen-only run, and a
        # final sweep otherwise (picks up regens queued for already-done fish).
        _drain_regen_queue(col, pngs)

        mode = "regen run" if regen_only else "batch"
        with _lock:
            _status.state = "stopped" if _stop_flag.is_set() else "finished"
            _status.finished_at = time.time()
            _status.current = ""
        _log_event(f"{mode} {_status.state} — "
                   f"done {_status.done} · skip {_status.skipped} · "
                   f"fail {_status.failed}")
    except Exception as e:  # noqa: BLE001
        with _lock:
            _status.state = "error"
            _status.error = repr(e)
            _status.finished_at = time.time()
        _log_event(f"{'regen run' if regen_only else 'batch'} ERROR — {e!r}")
    finally:
        keepalive_stop.set()


def _launch_worker(regen_only: bool) -> None:
    """Reset status and start the background worker thread. A full run
    (regen_only=False) clears the regen queue first; a regen-only run keeps
    it intact (the caller has just queued a fish). Caller must have already
    verified nothing is running."""
    global _thread
    with _lock:
        _status.state = "running"
        _status.total = 0
        _status.done = 0
        _status.skipped = 0
        _status.failed = 0
        _status.current = ""
        _status.error = None
        _status.last_error = None
        _status.started_at = time.time()
        _status.finished_at = None
        if not regen_only:
            _regen_queue.clear()
        _log.clear()
    _stop_flag.clear()
    _ensure_skip_set_loaded()
    _thread = threading.Thread(
        target=_batch_loop, kwargs={"regen_only": regen_only}, daemon=True)
    _thread.start()


router = APIRouter()


@router.get("/fishanims", response_class=HTMLResponse)
async def fishanims_page(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "fishanims.html")


@router.get("/spriteviewer", response_class=HTMLResponse)
async def spriteviewer_page(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "spriteviewer.html")


@router.get("/api/fishanims/list")
async def fishanims_list(request: Request) -> JSONResponse:
    try:
        _ensure_migrated()
        col = fish_anims_col()
        # Only current-format docs count (the `cols` field marks a 5x5
        # Veo sprite sheet); legacy docs are ignored so they don't show
        # as broken rows.
        cur = col.find(
            {"cols": {"$exists": True}},
            {"name": 1, "idx": 1, "cols": 1, "rows": 1,
             "frames": 1, "frameW": 1, "frameH": 1},
        ).sort([("name", 1), ("idx", 1)])

        rows_map: dict[str, list[dict]] = {}
        for d in cur:
            rows_map.setdefault(d["name"], []).append({
                "idx": str(d["idx"]),
                "cols": d.get("cols", 5),
                "rows": d.get("rows", 5),
                "frames": d.get("frames", 24),
                "frameW": d.get("frameW", 256),
                "frameH": d.get("frameH", 256),
            })

        # Make sure every disk-side species has a row even when it has no
        # generated clips yet — otherwise the user can't see an Upload
        # button for fish that haven't been animated yet.
        for stem in _disk_stems():
            rows_map.setdefault(stem, [])

        # Mark which species have a user-uploaded reference so the
        # frontend can surface a "custom ref" badge + a reset button.
        try:
            custom_refs = {
                d["name"]
                for d in fish_anims_refs_col().find({}, {"name": 1})
            }
        except Exception as e:  # noqa: BLE001
            print(f"[fishanims] read custom refs: {e}", flush=True)
            custom_refs = set()

        rows = [
            {
                "name": n,
                "sheets": s,
                "hasCustomRef": n in custom_refs,
            }
            for n, s in sorted(rows_map.items(), key=lambda kv: kv[0].lower())
            if n in ALLOWED_STEMS
        ]
        return JSONResponse({"rows": rows, "count": len(rows)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"rows": [], "count": 0, "error": str(e)})


def _fetch_sheet(name: str, idx: str) -> bytes:
    name = _safe_name(name)
    if not idx.isdigit():
        raise HTTPException(400, "bad idx")
    doc = fish_anims_col().find_one(
        {"name": name, "idx": int(idx)},
        {"sheet": 1},
    )
    if not doc or not doc.get("sheet"):
        raise HTTPException(404, "not found")
    return bytes(doc["sheet"])


@router.get("/api/fishanims/{name}/{idx}/sheet")
async def fishanims_sheet(name: str, idx: str, request: Request) -> Response:
    return Response(content=_fetch_sheet(name, idx), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


def _worker_running() -> bool:
    """True if the background worker thread is alive and marked running."""
    with _lock:
        return (_status.state == "running"
                and _thread is not None and _thread.is_alive())


@router.post("/api/fishanims/batch/start")
async def fishanims_batch_start(request: Request) -> JSONResponse:
    import veo_gen  # noqa: WPS433
    ok, why = veo_gen.veo_configured()
    if not ok:
        raise HTTPException(503, f"Veo not configured on server: {why}")
    if not INPUT_DIR.is_dir():
        raise HTTPException(503, f"input folder missing on server: {INPUT_DIR.name}")
    if _worker_running():
        raise HTTPException(409, "batch already running")
    # Persistent skips survive Start cycles; only the transient regen queue
    # is reset (handled inside _launch_worker for a full run).
    _launch_worker(regen_only=False)
    return JSONResponse({"ok": True, "state": "running"})


@router.post("/api/fishanims/batch/stop")
async def fishanims_batch_stop(request: Request) -> JSONResponse:
    _stop_flag.set()
    return JSONResponse({"ok": True})


@router.post("/api/fishanims/batch/skip/{name}")
async def fishanims_batch_skip(name: str, request: Request) -> JSONResponse:
    """Skip this fish — both the rest of the current row AND every future
    batch run, until explicitly unskipped. Persisted in MongoDB so the
    decision survives dyno restarts. The batch worker checks the in-memory
    cache at the top of each inner-loop iteration, so the in-flight
    generation finishes first then the row terminates."""
    name = _safe_name(name)
    fish_anims_skips_col().update_one(
        {"name": name},
        {"$set": {"name": name, "skipped_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    with _lock:
        _skip_fish.add(name)
    return JSONResponse({"ok": True, "skipped": name})


@router.post("/api/fishanims/batch/unskip/{name}")
async def fishanims_batch_unskip(name: str, request: Request) -> JSONResponse:
    """Reverse a previous skip. The fish becomes eligible for generation
    again on the next batch tick (or immediately if currently being skipped
    over). MongoDB doc is removed so the unskip persists too."""
    name = _safe_name(name)
    fish_anims_skips_col().delete_one({"name": name})
    with _lock:
        _skip_fish.discard(name)
    return JSONResponse({"ok": True, "unskipped": name})


@router.post("/api/fishanims/batch/regen/{name}")
async def fishanims_batch_regen(name: str, request: Request) -> JSONResponse:
    """Queue a fish for full regeneration (wipe its clips, re-run Veo).

    If a run is already in progress the fish just joins its regen queue.
    Otherwise a regen-ONLY worker is started that processes just the queued
    fish — it does NOT sweep every fish like a full batch. So clicking Regen
    on one fish only regenerates that fish."""
    name = _safe_name(name)
    import veo_gen  # noqa: WPS433
    ok, why = veo_gen.veo_configured()
    if not ok:
        raise HTTPException(503, f"Veo not configured on server: {why}")
    with _lock:
        if name not in _regen_queue:
            _regen_queue.append(name)
    if not _worker_running():
        _launch_worker(regen_only=True)
    return JSONResponse({"ok": True, "queued": name})


@router.get("/api/fishanims/batch/status")
async def fishanims_batch_status(request: Request) -> JSONResponse:
    _ensure_skip_set_loaded()
    with _lock:
        return JSONResponse({
            "state": _status.state,
            "total": _status.total,
            "done": _status.done,
            "skipped": _status.skipped,
            "failed": _status.failed,
            "current": _status.current,
            "error": _status.error,
            "last_error": _status.last_error,
            "started_at": _status.started_at,
            "finished_at": _status.finished_at,
            # Filter to ALLOWED_STEMS — persisted skips survive deploys, so
            # an adult fish skipped before the allowlist existed would
            # otherwise be re-added as a row by the frontend's augmentRows.
            "skipped_fish": sorted(s for s in _skip_fish if s in ALLOWED_STEMS),
            "regen_queue": [r for r in _regen_queue if r in ALLOWED_STEMS],
            "log": list(_log),
        })


@router.get("/api/fishanims/prompt")
async def fishanims_get_prompt(request: Request) -> JSONResponse:
    """Return the Veo prompt used for generation — the saved override if
    one exists, plus the built-in default so the UI can offer a reset."""
    import veo_gen  # noqa: WPS433
    current = _get_veo_prompt()
    return JSONResponse({
        "prompt": current,
        "default": veo_gen.VEO_PROMPT,
        "is_default": current == veo_gen.VEO_PROMPT,
    })


@router.post("/api/fishanims/prompt")
async def fishanims_set_prompt(request: Request) -> JSONResponse:
    """Save the Veo prompt. The next fish generated (including fish still
    queued in a running batch) uses it. A blank prompt clears the override
    so the built-in default is used again."""
    import veo_gen  # noqa: WPS433
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "expected a JSON body")
    prompt = (body.get("prompt") or "").strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            413, f"prompt too long ({len(prompt)} > {MAX_PROMPT_CHARS} chars)")
    col = fish_anims_settings_col()
    if not prompt:
        col.delete_one({"key": _VEO_PROMPT_KEY})
        return JSONResponse({
            "ok": True, "prompt": veo_gen.VEO_PROMPT, "is_default": True})
    col.update_one(
        {"key": _VEO_PROMPT_KEY},
        {"$set": {
            "key": _VEO_PROMPT_KEY,
            "value": prompt,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return JSONResponse({
        "ok": True, "prompt": prompt,
        "is_default": prompt == veo_gen.VEO_PROMPT,
    })


@router.get("/api/fishanims/{name}/{idx}/download")
async def fishanims_download(name: str, idx: str, request: Request) -> Response:
    return Response(
        content=_fetch_sheet(name, idx),
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{name}_{idx}.png"',
            "Cache-Control": "no-store",
        },
    )


# ----- Per-fish reference image upload --------------------------------------
# The batch worker reads the reference image when animating each fish with
# Veo. By default that's the on-disk PNG in 'Chinesely Fish (256)/', but
# users can upload a replacement here that gets persisted to MongoDB and
# preferred by `_get_ref_bytes`. Re-running Regen for the fish then
# produces fresh clips driven by the uploaded image.

@router.get("/api/fishanims/{name}/ref")
async def fishanims_get_ref(name: str, request: Request) -> Response:
    """Serve the current reference image composited onto a solid black
    background — uploaded ref if present, otherwise the on-disk default.
    The black backing matches what Veo is fed, so the web preview shows
    exactly the image the clips are generated from. Returns 404 only when
    neither source has bytes (e.g. an unknown species name)."""
    name = _safe_name(name)
    doc = fish_anims_refs_col().find_one(
        {"name": name},
        {"ref": 1},
    )
    if doc and doc.get("ref"):
        return Response(
            content=_flatten_to_black(bytes(doc["ref"])),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
    disk = INPUT_DIR / f"{name}.png"
    if disk.exists():
        return Response(
            content=_flatten_to_black(disk.read_bytes()),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
    raise HTTPException(404, "no reference image for this fish")


@router.post("/api/fishanims/{name}/ref")
async def fishanims_upload_ref(
    name: str,
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    """Replace the reference image for `name`. Stored in MongoDB so it
    survives Render dyno restarts. The next Regen for this fish will use
    the uploaded image instead of the on-disk default."""
    name = _safe_name(name)
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_REF_TYPES:
        raise HTTPException(
            400,
            f"unsupported image type '{content_type}' "
            f"(use png, jpg, or webp)",
        )
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > MAX_REF_BYTES:
        raise HTTPException(
            413,
            f"file too large ({len(data)//1024}KB > "
            f"{MAX_REF_BYTES//1024}KB max)",
        )
    fish_anims_refs_col().update_one(
        {"name": name},
        {"$set": {
            "name": name,
            "ref": Binary(data),
            "content_type": content_type,
            "uploaded_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    # Stale clips were generated from the OLD ref — drop them so the
    # gallery never shows an animation that doesn't match the new ref,
    # and so a plain "Start" regenerates this fish instead of skipping it
    # (the batch loop skips any fish that already has PER_FISH sheets).
    wiped = fish_anims_col().delete_many({"name": name}).deleted_count
    return JSONResponse({
        "ok": True,
        "name": name,
        "bytes": len(data),
        "content_type": content_type,
        "clips_wiped": wiped,
    })


@router.delete("/api/fishanims/{name}/ref")
async def fishanims_delete_ref(name: str, request: Request) -> JSONResponse:
    """Drop the uploaded reference for `name` so the next generation
    falls back to the on-disk default. No-op if no upload exists."""
    name = _safe_name(name)
    res = fish_anims_refs_col().delete_one({"name": name})
    return JSONResponse({"ok": True, "deleted": res.deleted_count})
