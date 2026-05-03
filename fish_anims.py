"""Fish animations gallery. Generation runs in a background thread on
the prod server (kicked off by the local trigger_batch.py). A
keepalive pinger fires the public URL every minute while the batch
runs, so Render's free-tier dyno doesn't spin down mid-job.

Sheets live on disk under webapp/fish_anims/. The gallery JS polls
list + batch/status every 5s, so viewers also see progress live.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

APP_DIR = Path(__file__).resolve().parent
ANIMS_DIR = APP_DIR / "fish_anims"
STATIC_DIR = APP_DIR / "static"
INPUT_DIR = APP_DIR / "Chinesely Fish (256)"
PER_FISH = 5
KEEPALIVE_SECS = 60

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _batch_key() -> str:
    # Read each call so a Render env-var update is picked up without a restart.
    return os.getenv("BATCH_UPLOAD_KEY", "")


def _require_batch_key(request: Request) -> None:
    expected = _batch_key()
    if not expected:
        raise HTTPException(503, "BATCH_UPLOAD_KEY not configured on server")
    if request.headers.get("x-batch-key") != expected:
        raise HTTPException(401, "bad batch key")


def _require_auth(request: Request) -> None:
    from app import _require_fish_auth as _ra  # noqa: WPS433
    _ra(request)


def _is_authed(request: Request) -> bool:
    from app import _is_fish_authed as _ia  # noqa: WPS433
    return _ia(request)


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


def _keepalive_loop(stop_event: threading.Event) -> None:
    """Hit the public URL every minute so Render's dyno doesn't spin down.
    Free-tier inactivity is measured by external HTTP traffic, so we go
    out → internet → load balancer → back into the container."""
    import requests  # available via app's deps

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


def _batch_loop() -> None:
    keepalive_stop = threading.Event()
    keepalive_thread = threading.Thread(
        target=_keepalive_loop, args=(keepalive_stop,), daemon=True)
    try:
        from app import _generate_one  # noqa: WPS433

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
            species_dir = ANIMS_DIR / stem
            species_dir.mkdir(parents=True, exist_ok=True)
            ref_bytes = png.read_bytes()
            for idx in range(1, PER_FISH + 1):
                if _stop_flag.is_set():
                    break
                sheet_path = species_dir / f"{idx}.png"
                meta_path = species_dir / f"{idx}.json"
                with _lock:
                    _status.current = f"{stem} {idx}/{PER_FISH}"
                if sheet_path.exists() and meta_path.exists():
                    with _lock:
                        _status.skipped += 1
                    continue
                sheet_bytes = frames = frame_w = None
                last_err: Exception | None = None
                for attempt in (1, 2):
                    try:
                        sheet_bytes, frames, frame_w = _generate_one(ref_bytes)
                        break
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        print(f"[fishanims] try {attempt} {stem}/{idx}: {e}",
                              flush=True)
                        if attempt < 2:
                            time.sleep(5)
                if sheet_bytes is None:
                    with _lock:
                        _status.failed += 1
                    print(f"[fishanims] FAIL {stem}/{idx}: {last_err}", flush=True)
                    continue
                sheet_path.write_bytes(sheet_bytes)
                meta_path.write_text(json.dumps({
                    "frames": frames,
                    "frameW": frame_w,
                    "frameH": frame_w,
                    "created_at": time.time(),
                }))
                with _lock:
                    _status.done += 1
                print(f"[fishanims] ok {stem}/{idx} ({frames}f)", flush=True)

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
    if not _is_authed(request):
        from fastapi.responses import RedirectResponse  # noqa: WPS433
        return RedirectResponse("/?tab=fish")
    return FileResponse(STATIC_DIR / "fishanims.html")


@router.get("/api/fishanims/list")
async def fishanims_list(request: Request) -> JSONResponse:
    _require_auth(request)
    rows: list[dict] = []
    if ANIMS_DIR.is_dir():
        for sub in sorted(ANIMS_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not sub.is_dir():
                continue
            sheets: list[dict] = []
            for meta_path in sorted(sub.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
                idx = meta_path.stem
                sheet_path = sub / f"{idx}.png"
                if not sheet_path.exists():
                    continue
                try:
                    meta = json.loads(meta_path.read_text())
                except Exception:  # noqa: BLE001
                    continue
                sheets.append({
                    "idx": idx,
                    "frames": meta.get("frames", 1),
                    "frameW": meta.get("frameW", 256),
                    "frameH": meta.get("frameH", 256),
                })
            if sheets:
                rows.append({"name": sub.name, "sheets": sheets})
    return JSONResponse({"rows": rows, "count": len(rows)})


def _resolve(name: str, idx: str) -> Path:
    name = _safe_name(name)
    if not idx.isdigit():
        raise HTTPException(400, "bad idx")
    p = ANIMS_DIR / name / f"{idx}.png"
    if not p.exists():
        raise HTTPException(404, "not found")
    return p


@router.get("/api/fishanims/{name}/{idx}/sheet")
async def fishanims_sheet(name: str, idx: str, request: Request) -> Response:
    _require_auth(request)
    p = _resolve(name, idx)
    return Response(content=p.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/fishanims/batch/start")
async def fishanims_batch_start(request: Request) -> JSONResponse:
    _require_batch_key(request)
    if not os.getenv("PIXELLAB_SECRET"):
        raise HTTPException(503, "PIXELLAB_SECRET not configured on server")
    if not INPUT_DIR.is_dir():
        raise HTTPException(503, f"input folder missing on server: {INPUT_DIR.name}")
    global _thread
    with _lock:
        # Only block if a thread is genuinely alive — after a dyno crash the
        # old status may say "running" even though no thread exists, so allow
        # the user to re-trigger and resume (skip-existing handles dedup).
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
    _thread = threading.Thread(target=_batch_loop, daemon=True)
    _thread.start()
    return JSONResponse({"ok": True, "state": "running"})


@router.post("/api/fishanims/batch/stop")
async def fishanims_batch_stop(request: Request) -> JSONResponse:
    _require_batch_key(request)
    _stop_flag.set()
    return JSONResponse({"ok": True})


@router.get("/api/fishanims/batch/status")
async def fishanims_batch_status(request: Request) -> JSONResponse:
    _require_auth(request)
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
        })


@router.get("/api/fishanims/{name}/{idx}/download")
async def fishanims_download(name: str, idx: str, request: Request) -> Response:
    _require_auth(request)
    p = _resolve(name, idx)
    return Response(
        content=p.read_bytes(),
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{name}_{idx}.png"',
            "Cache-Control": "no-store",
        },
    )
