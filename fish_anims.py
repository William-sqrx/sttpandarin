"""Gallery for the batch-animated sprite sheets produced by
batch_animate_fish.py. Each fish folder gets one row in the UI; each
sheet is rendered as an animated canvas with a download button. Sheets
live on disk under webapp/fish_anims/, so the page is fully static —
restarting the server does not lose state.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

APP_DIR = Path(__file__).resolve().parent
ANIMS_DIR = APP_DIR / "fish_anims"
STATIC_DIR = APP_DIR / "static"

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


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
