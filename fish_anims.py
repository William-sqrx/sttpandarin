"""Gallery for the batch-animated sprite sheets produced by
batch_animate_fish.py. Each fish folder gets one row in the UI; each
sheet is rendered as an animated canvas with a download button. Sheets
live on disk under webapp/fish_anims/, so the page is fully static —
restarting the server does not lose state.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

APP_DIR = Path(__file__).resolve().parent
ANIMS_DIR = APP_DIR / "fish_anims"
STATIC_DIR = APP_DIR / "static"

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


@router.post("/api/fishanims/{name}/{idx}/upload")
async def fishanims_upload(name: str, idx: str, request: Request) -> JSONResponse:
    """Accept a generated sprite sheet from the local batch script. Auth
    via x-batch-key header (matches BATCH_UPLOAD_KEY env var). Writes
    <name>/<idx>.png and <name>/<idx>.json to ANIMS_DIR.
    """
    _require_batch_key(request)
    name = _safe_name(name)
    if not idx.isdigit():
        raise HTTPException(400, "bad idx")
    species_dir = ANIMS_DIR / name
    species_dir.mkdir(parents=True, exist_ok=True)

    form = await request.form()
    sheet = form.get("sheet")
    meta_raw = form.get("meta")
    if sheet is None or meta_raw is None:
        raise HTTPException(400, "need 'sheet' (file) + 'meta' (json)")
    sheet_bytes = await sheet.read()
    if not sheet_bytes:
        raise HTTPException(400, "empty sheet")
    try:
        meta = json.loads(meta_raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"bad meta json: {e}")

    (species_dir / f"{idx}.png").write_bytes(sheet_bytes)
    (species_dir / f"{idx}.json").write_text(json.dumps(meta))
    return JSONResponse({"ok": True, "name": name, "idx": idx,
                         "bytes": len(sheet_bytes)})


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
