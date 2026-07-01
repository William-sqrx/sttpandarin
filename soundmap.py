"""Pinyin sound-map reader/editor page.

Serves a static, password-gated page (/soundmap) that renders the app's
pinyin → familiar-word "sound map" (the initial + final → sound-alike tables
from chinesely-frontend/src/lib/pinyinSoundMap.js) for BOTH English and Bahasa,
in an easy-to-read, editable form. All data + editing lives client-side in
static/soundmap.{html,css,js,data.js}; there's no server state, so this module
is just the auth gate + page route (mirrors imagegen.py's pattern).
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter()


@router.get("/soundmap", response_class=HTMLResponse)
async def soundmap_page(request: Request) -> Response:
    from app import _is_authed  # noqa: WPS433 — avoid circular import at module load
    if not _is_authed(request):
        return RedirectResponse("/")
    return FileResponse(STATIC_DIR / "soundmap.html")
