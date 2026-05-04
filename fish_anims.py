"""Fish animations gallery. Generation runs in a background thread on
the prod server (kicked off by trigger_batch.py). Sheets are stored in
MongoDB so they survive Render redeploys + dyno restarts.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bson import Binary
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from db import fish_anims_col

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
INPUT_DIR = APP_DIR / "Chinesely Fish (256)"
PER_FISH = 5
KEEPALIVE_SECS = 60

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _safe_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise HTTPException(400, "bad name")
    return name


# ----- Batch background runner ----------------------------------------------

@dataclass
class _BatchStatus:
    state: str = "idle"   # idle | running | finished | stopped | error
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    current: str = ""
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


_status = _BatchStatus()
_lock = threading.Lock()
_stop_flag = threading.Event()
_thread: threading.Thread | None = None
_migrated_once = False
_skip_fish: set[str] = set()
_regen_queue: list[str] = []  # FIFO of fish to fully regenerate (5 fresh sheets)


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


def _generate_and_save_one(col, stem: str, idx: int, ref_bytes: bytes,
                           label: str = "") -> bool:
    """Generate one sprite sheet via PixelLab and persist to MongoDB.
    Updates _status.done/_status.failed counters. Returns True on success.
    Caller is responsible for skip-existing logic."""
    from app import _generate_one  # noqa: WPS433

    sheet_bytes = frames = frame_w = None
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            sheet_bytes, frames, frame_w = _generate_one(ref_bytes)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[fishanims] try {attempt} {stem}/{idx}{label}: {e}",
                  flush=True)
            if attempt < 2:
                time.sleep(5)
    if sheet_bytes is None:
        with _lock:
            _status.failed += 1
        print(f"[fishanims] FAIL {stem}/{idx}{label}: {last_err}", flush=True)
        return False

    doc = {
        "name": stem,
        "idx": idx,
        "sheet": Binary(sheet_bytes),
        "frames": frames,
        "frameW": frame_w,
        "frameH": frame_w,
        "created_at": datetime.now(timezone.utc),
    }
    for db_attempt in range(1, 7):  # ~5s, 10s, 20s, 40s, 60s, 60s
        try:
            col.update_one(
                {"name": stem, "idx": idx},
                {"$set": doc},
                upsert=True,
            )
            if not col.find_one({"name": stem, "idx": idx}, {"_id": 1}):
                raise RuntimeError("upsert returned ok but doc not found")
            with _lock:
                _status.done += 1
            print(f"[fishanims] ok {stem}/{idx}{label} ({frames}f, "
                  f"{len(sheet_bytes)//1024}KB → mongo)", flush=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[fishanims] mongo write {db_attempt} {stem}/{idx}{label}: {e}",
                  flush=True)
            if db_attempt < 6:
                backoff = min(60, 5 * (2 ** (db_attempt - 1)))
                time.sleep(backoff)
    with _lock:
        _status.failed += 1
    print(f"[fishanims] PERMANENT MONGO WRITE FAIL {stem}/{idx}{label} "
          f"(sheet bytes lost — re-trigger to regenerate)", flush=True)
    return False


def _drain_regen_queue(col, pngs) -> None:
    """Process every pending regen request: wipe the fish's existing sheets
    and regenerate all 5. Bumps _status.total so the progress bar accounts
    for the extra work. Honors stop flag between sheets."""
    while True:
        with _lock:
            if not _regen_queue or _stop_flag.is_set():
                return
            stem = _regen_queue.pop(0)
        png = next((p for p in pngs if p.stem == stem), None)
        if png is None:
            print(f"[fishanims] regen: unknown fish '{stem}', skipped",
                  flush=True)
            continue
        deleted = col.delete_many({"name": stem}).deleted_count
        print(f"[fishanims] regen: wiping {stem} ({deleted} existing) → 5 fresh",
              flush=True)
        with _lock:
            _status.total += PER_FISH
        ref_bytes = png.read_bytes()
        for idx in range(1, PER_FISH + 1):
            if _stop_flag.is_set():
                return
            with _lock:
                _status.current = f"{stem} {idx}/{PER_FISH} (regen)"
            _generate_and_save_one(col, stem, idx, ref_bytes, label=" regen")


def _batch_loop() -> None:
    keepalive_stop = threading.Event()
    keepalive_thread = threading.Thread(
        target=_keepalive_loop, args=(keepalive_stop,), daemon=True)
    try:
        from app import _generate_one  # noqa: WPS433,F401
        col = fish_anims_col()
        _ensure_migrated()

        if not INPUT_DIR.is_dir():
            with _lock:
                _status.state = "error"
                _status.error = f"input folder not found: {INPUT_DIR}"
                _status.finished_at = time.time()
            return

        pngs = sorted(p for p in INPUT_DIR.glob("*.png")
                      if any(c.isalpha() for c in p.stem))
        with _lock:
            _status.total = len(pngs) * PER_FISH

        keepalive_thread.start()

        for png in pngs:
            if _stop_flag.is_set():
                break
            stem = png.stem
            ref_bytes = png.read_bytes()
            for idx in range(1, PER_FISH + 1):
                if _stop_flag.is_set():
                    break
                with _lock:
                    if stem in _skip_fish:
                        # Bump remaining slots into the skipped counter so
                        # the progress bar reaches `total` even when fish
                        # are short-circuited mid-row.
                        _status.skipped += (PER_FISH - idx + 1)
                        break
                    _status.current = f"{stem} {idx}/{PER_FISH}"

                if col.find_one({"name": stem, "idx": idx}, {"_id": 1}):
                    with _lock:
                        _status.skipped += 1
                    continue

                _generate_and_save_one(col, stem, idx, ref_bytes)
                # After every sheet save, drain any regen requests that
                # arrived during this sheet's generation. This makes regen
                # take effect "as soon as the in-flight sheet finishes",
                # rather than waiting until the entire batch is done.
                _drain_regen_queue(col, pngs)

        # End-of-batch sweep — picks up regen requests issued for fish the
        # main loop has already finished with.
        _drain_regen_queue(col, pngs)

        with _lock:
            _status.state = "stopped" if _stop_flag.is_set() else "finished"
            _status.finished_at = time.time()
            _status.current = ""
    except Exception as e:  # noqa: BLE001
        with _lock:
            _status.state = "error"
            _status.error = repr(e)
            _status.finished_at = time.time()
    finally:
        keepalive_stop.set()


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
        cur = col.find(
            {},
            {"name": 1, "idx": 1, "frames": 1, "frameW": 1, "frameH": 1},
        ).sort([("name", 1), ("idx", 1)])

        rows_map: dict[str, list[dict]] = {}
        for d in cur:
            rows_map.setdefault(d["name"], []).append({
                "idx": str(d["idx"]),
                "frames": d.get("frames", 1),
                "frameW": d.get("frameW", 256),
                "frameH": d.get("frameH", 256),
            })
        rows = [
            {"name": n, "sheets": s}
            for n, s in sorted(rows_map.items(), key=lambda kv: kv[0].lower())
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
    if not doc:
        raise HTTPException(404, "not found")
    return bytes(doc["sheet"])


@router.get("/api/fishanims/{name}/{idx}/sheet")
async def fishanims_sheet(name: str, idx: str, request: Request) -> Response:
    return Response(content=_fetch_sheet(name, idx), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/fishanims/batch/start")
async def fishanims_batch_start(request: Request) -> JSONResponse:
    if not os.getenv("PIXELLAB_SECRET"):
        raise HTTPException(503, "PIXELLAB_SECRET not configured on server")
    if not INPUT_DIR.is_dir():
        raise HTTPException(503, f"input folder missing on server: {INPUT_DIR.name}")
    global _thread
    with _lock:
        if _status.state == "running" and _thread is not None and _thread.is_alive():
            raise HTTPException(409, "batch already running")
        _status.state = "running"
        _status.total = 0
        _status.done = 0
        _status.skipped = 0
        _status.failed = 0
        _status.current = ""
        _status.error = None
        _status.started_at = time.time()
        _status.finished_at = None
    _stop_flag.clear()
    with _lock:
        _skip_fish.clear()
        _regen_queue.clear()
    _thread = threading.Thread(target=_batch_loop, daemon=True)
    _thread.start()
    return JSONResponse({"ok": True, "state": "running"})


@router.post("/api/fishanims/batch/stop")
async def fishanims_batch_stop(request: Request) -> JSONResponse:
    _stop_flag.set()
    return JSONResponse({"ok": True})


@router.post("/api/fishanims/batch/skip/{name}")
async def fishanims_batch_skip(name: str, request: Request) -> JSONResponse:
    """Skip the rest of this fish's slots and move to the next species. The
    batch worker checks this set at the top of each inner-loop iteration,
    so the in-flight generation finishes first then the row terminates."""
    name = _safe_name(name)
    with _lock:
        _skip_fish.add(name)
    return JSONResponse({"ok": True, "skipped": name})


@router.post("/api/fishanims/batch/regen/{name}")
async def fishanims_batch_regen(name: str, request: Request) -> JSONResponse:
    """Queue a fish for full regeneration: after the in-flight sheet finishes,
    the worker wipes all 5 sheets for this fish from MongoDB and generates
    5 fresh ones. _status.total is bumped so the progress bar accounts for
    the extra work. Idempotent — re-queuing an already-pending fish is a no-op."""
    name = _safe_name(name)
    with _lock:
        if name not in _regen_queue:
            _regen_queue.append(name)
    return JSONResponse({"ok": True, "queued": name})


@router.get("/api/fishanims/batch/status")
async def fishanims_batch_status(request: Request) -> JSONResponse:
    with _lock:
        return JSONResponse({
            "state": _status.state,
            "total": _status.total,
            "done": _status.done,
            "skipped": _status.skipped,
            "failed": _status.failed,
            "current": _status.current,
            "error": _status.error,
            "started_at": _status.started_at,
            "finished_at": _status.finished_at,
            "skipped_fish": sorted(_skip_fish),
            "regen_queue": list(_regen_queue),
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
