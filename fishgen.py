"""Fish generator — OpenAI gpt-image-1 powered grid for generating
adult / teen / baby variants of every species, plus PixelLab sprite
sheet animation. UI lives at /fishgen.

This module is mounted onto the main FastAPI app via
``app.include_router(fishgen.router)`` at the bottom of app.py. Auth
and sprite helpers are imported from app at function-call time to
avoid circular-import surprises at module load.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
)
from pydantic import BaseModel

import requests

# ----- Config (env-driven) ---------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")

APP_DIR = Path(__file__).resolve().parent
FG_DIR = APP_DIR / "fishgen"
FG_DIR.mkdir(exist_ok=True)
STATIC_DIR = APP_DIR / "static"

# ----- Stages + species ------------------------------------------------------

# Three stages we generate, in dependency order. Each entry's `ref` is
# the stage to seed from when generating (None = pure text-to-image).
STAGES: list[dict] = [
    {"key": "adult", "label": "Adult", "ref": None},
    {"key": "teen",  "label": "Teen",  "ref": "adult"},
    {"key": "baby",  "label": "Baby",  "ref": "teen"},
]
STAGE_KEYS = {s["key"] for s in STAGES}

# Real fish species. Mirrors fishConfig.js with the placeholder rows
# ("Fish 1", "Fish 2", "Adult Angelfish") filtered out.
SPECIES: list[str] = [
    "Angelfish", "Axolotl", "Blobfish", "Jawfish", "Boxfish",
    "Clownfish", "Coral Beauty", "Dolphin", "Arowana", "Hammerhead",
    "Rasbora", "Harlequin", "Jellyfish", "Koi", "Sea Dragon",
    "Lionfish", "Mandarin", "Manta Ray", "Octopus", "Pufferfish",
    "Trout", "Royal Gramma", "Seahorse", "Shark", "Sturgeon",
    "Sunfish", "Swordfish", "Tuna", "Whale", "Yellow Tang", "Zebrafish",
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


SLUG_TO_NAME = {_slug(n): n for n in SPECIES}


def _stage_dir(species_slug: str, stage: str) -> Path:
    if species_slug not in SLUG_TO_NAME:
        raise HTTPException(404, f"unknown species: {species_slug}")
    if stage not in STAGE_KEYS:
        raise HTTPException(404, f"unknown stage: {stage}")
    p = FG_DIR / species_slug / stage
    p.mkdir(parents=True, exist_ok=True)
    return p


def _default_prompt(species: str, stage: str) -> str:
    """Stage-specific default prompts. Editable per fish per stage."""
    if stage == "adult":
        return (
            f"A single kawaii cartoon adult {species}, side view, body "
            f"facing LEFT. Flat 2D illustration, fully transparent PNG "
            f"background. Soft watercolour cartoon rendering with kawaii "
            f"face — small round eyes, small smiling mouth. Plump rounded "
            f"body, full mature fin shapes and patterns. Clean outline "
            f"(deep tone, consistent thickness), flat colour fill with "
            f"subtle watercolour texture. NO photo realism, NO 3D, NO "
            f"sheen or highlight, NO drop shadow. Square canvas."
        )
    if stage == "teen":
        return (
            f"A single kawaii cartoon teen {species}, in the EXACT SAME "
            f"art style as the attached reference image — same outline "
            f"thickness, same watercolour texture, same kawaii face "
            f"treatment, same canvas resolution. Flat 2D side-view "
            f"illustration, body facing LEFT. Fully transparent PNG "
            f"background.\n\n"
            f"Match the colour palette and rendering of the adult "
            f"reference, but the silhouette is younger — slightly rounder "
            f"body, slightly smaller fins, simpler tail (a fork rather "
            f"than a full fan), patterns just beginning to appear. The "
            f"feel is 'almost adult, but not quite' — markings are "
            f"sparser and less defined.\n\n"
            f"GUARDRAILS: same colours as adult; flat watercolour only; "
            f"no sheen or shine; no extra body parts; no background."
        )
    return (
        f"A single kawaii cartoon baby {species}, in the EXACT SAME art "
        f"style as the attached teen reference image — same outline "
        f"thickness, same watercolour texture, same kawaii face "
        f"treatment, same canvas resolution. Flat 2D side-view "
        f"illustration, body facing LEFT. Fully transparent PNG "
        f"background.\n\n"
        f"The baby is the simplest form: very rounded, chubby silhouette, "
        f"oversized head relative to body, big sparkly eyes, tiny fins. "
        f"Pattern is minimal or absent — mostly the base body colour. "
        f"Tail is short and simple. Reads as a fish hatchling, "
        f"unmistakably the same species but adorable + tiny.\n\n"
        f"GUARDRAILS: same base colour family as the reference; flat "
        f"watercolour only; no sheen; no extra body parts; no background."
    )


def _stage_meta(species_slug: str, stage: str) -> dict:
    """Tiny JSON describing what's in this stage's folder."""
    d = FG_DIR / species_slug / stage
    img = d / "image.png"
    sheet = d / "sheet.png"
    prompt = d / "prompt.txt"
    return {
        "species": SLUG_TO_NAME.get(species_slug, species_slug),
        "slug": species_slug,
        "stage": stage,
        "has_image": img.exists(),
        "has_sheet": sheet.exists(),
        "image_mtime": img.stat().st_mtime if img.exists() else None,
        "sheet_mtime": sheet.stat().st_mtime if sheet.exists() else None,
        "prompt": prompt.read_text() if prompt.exists() else "",
    }


# ----- OpenAI calls ----------------------------------------------------------

def _openai_text_to_image(prompt: str) -> bytes:
    if not OPENAI_API_KEY:
        raise HTTPException(400, "OPENAI_API_KEY not configured on server")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "background": "transparent",
        "quality": "high",
        "output_format": "png",
    }
    try:
        r = requests.post(
            f"{OPENAI_BASE}/images/generations",
            headers=headers, json=body, timeout=300,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"OpenAI request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"OpenAI {r.status_code}: {r.text[:400]}")
    data = r.json().get("data") or []
    if not data or not data[0].get("b64_json"):
        raise HTTPException(502, f"OpenAI returned no image: {r.text[:200]}")
    return base64.b64decode(data[0]["b64_json"])


def _openai_image_edit(prompt: str, ref_png: bytes) -> bytes:
    if not OPENAI_API_KEY:
        raise HTTPException(400, "OPENAI_API_KEY not configured on server")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = {"image": ("ref.png", ref_png, "image/png")}
    data = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "n": "1",
        "size": "1024x1024",
        "background": "transparent",
        "quality": "high",
        "output_format": "png",
    }
    try:
        r = requests.post(
            f"{OPENAI_BASE}/images/edits",
            headers=headers, data=data, files=files, timeout=300,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"OpenAI request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"OpenAI {r.status_code}: {r.text[:400]}")
    payload = r.json().get("data") or []
    if not payload or not payload[0].get("b64_json"):
        raise HTTPException(502, f"OpenAI returned no image: {r.text[:200]}")
    return base64.b64decode(payload[0]["b64_json"])


# ----- Auth bridge -----------------------------------------------------------

def _require_auth(request: Request) -> None:
    """Defer to app.py's auth helper. Imported at call time so both
    modules can be imported in any order."""
    from app import _require_auth as _ra  # noqa: WPS433
    _ra(request)


def _is_authed(request: Request) -> bool:
    from app import _is_authed as _ia  # noqa: WPS433
    return _ia(request)


# ----- Router + routes -------------------------------------------------------

router = APIRouter()


@router.get("/fishgen", response_class=HTMLResponse)
async def fishgen_page(request: Request) -> Response:
    if not _is_authed(request):
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "fishgen.html")


@router.get("/api/fishgen/list")
async def fishgen_list(request: Request) -> JSONResponse:
    _require_auth(request)
    # Lazy-read PIXELLAB_SECRET each call so a server-side env update
    # is reflected without restart.
    from app import PIXELLAB_SECRET  # noqa: WPS433
    rows = []
    for name in SPECIES:
        slug = _slug(name)
        rows.append({
            "name": name,
            "slug": slug,
            "stages": [_stage_meta(slug, s["key"]) for s in STAGES],
        })
    return JSONResponse({
        "species": rows,
        "stages": STAGES,
        "openai_configured": bool(OPENAI_API_KEY),
        "pixellab_configured": bool(PIXELLAB_SECRET),
    })


@router.get("/api/fishgen/{slug}/{stage}/prompt")
async def fishgen_get_prompt(slug: str, stage: str,
                             request: Request) -> JSONResponse:
    _require_auth(request)
    d = _stage_dir(slug, stage)
    p = d / "prompt.txt"
    if p.exists():
        return JSONResponse({"prompt": p.read_text(), "default": False})
    return JSONResponse({
        "prompt": _default_prompt(SLUG_TO_NAME[slug], stage),
        "default": True,
    })


class PromptBody(BaseModel):
    prompt: str


@router.post("/api/fishgen/{slug}/{stage}/prompt")
async def fishgen_save_prompt(slug: str, stage: str, body: PromptBody,
                              request: Request) -> JSONResponse:
    _require_auth(request)
    d = _stage_dir(slug, stage)
    (d / "prompt.txt").write_text(body.prompt)
    return JSONResponse({"ok": True})


@router.get("/api/fishgen/{slug}/{stage}/image")
async def fishgen_image(slug: str, stage: str, request: Request) -> Response:
    _require_auth(request)
    d = _stage_dir(slug, stage)
    p = d / "image.png"
    if not p.exists():
        raise HTTPException(404, "no image yet")
    return Response(content=p.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/fishgen/{slug}/{stage}/sheet")
async def fishgen_sheet(slug: str, stage: str, request: Request) -> Response:
    _require_auth(request)
    d = _stage_dir(slug, stage)
    p = d / "sheet.png"
    if not p.exists():
        raise HTTPException(404, "no sheet yet")
    return Response(content=p.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.delete("/api/fishgen/{slug}/{stage}/image")
async def fishgen_delete_image(slug: str, stage: str,
                               request: Request) -> JSONResponse:
    _require_auth(request)
    d = _stage_dir(slug, stage)
    for fn in ("image.png", "sheet.png", "sheet_meta.json"):
        try:
            (d / fn).unlink()
        except FileNotFoundError:
            pass
    return JSONResponse({"ok": True})


class GenerateBody(BaseModel):
    prompt: str
    save_prompt: bool = True


@router.post("/api/fishgen/{slug}/{stage}/generate")
async def fishgen_generate(slug: str, stage: str, body: GenerateBody,
                           request: Request) -> JSONResponse:
    """Generate a still image. Adult is text-to-image; teen seeds from
    the saved adult; baby seeds from the saved teen."""
    _require_auth(request)
    d = _stage_dir(slug, stage)
    species_name = SLUG_TO_NAME[slug]
    if body.save_prompt:
        (d / "prompt.txt").write_text(body.prompt)

    ref_stage = next((s["ref"] for s in STAGES if s["key"] == stage), None)
    if ref_stage is None:
        png_bytes = _openai_text_to_image(body.prompt)
    else:
        ref_path = FG_DIR / slug / ref_stage / "image.png"
        if not ref_path.exists():
            raise HTTPException(
                400,
                f"need a {ref_stage} image first (generate that, then "
                f"come back to {stage})",
            )
        png_bytes = _openai_image_edit(body.prompt, ref_path.read_bytes())

    (d / "image.png").write_bytes(png_bytes)
    # Wipe old sprite sheet — it was animated from the old image.
    for fn in ("sheet.png", "sheet_meta.json"):
        try:
            (d / fn).unlink()
        except FileNotFoundError:
            pass
    return JSONResponse({
        "ok": True,
        "meta": _stage_meta(slug, stage),
        "species": species_name,
    })


@router.post("/api/fishgen/{slug}/{stage}/animate")
async def fishgen_animate(slug: str, stage: str,
                          request: Request) -> JSONResponse:
    """Generate the swim-in-place sprite sheet from the saved still
    image. Reuses the existing PixelLab pipeline (`_generate_one` from
    app.py). Synchronous — returns when the sheet is written."""
    _require_auth(request)
    from app import (  # noqa: WPS433
        PIXELLAB_SECRET,
        _generate_one,
        _FS_FRAME,
    )
    if not PIXELLAB_SECRET:
        raise HTTPException(400, "PIXELLAB_SECRET not configured on server")
    d = _stage_dir(slug, stage)
    ref_path = d / "image.png"
    if not ref_path.exists():
        raise HTTPException(400, "generate the still image first")
    sheet_bytes, n_frames = _generate_one(ref_path.read_bytes())
    (d / "sheet.png").write_bytes(sheet_bytes)
    (d / "sheet_meta.json").write_text(json.dumps({
        "frames": n_frames,
        "frameW": _FS_FRAME,
        "frameH": _FS_FRAME,
        "created_at": time.time(),
    }))
    return JSONResponse({
        "ok": True,
        "frames": n_frames,
        "frameW": _FS_FRAME,
        "frameH": _FS_FRAME,
        "meta": _stage_meta(slug, stage),
    })
