"""Image generation studio — upload one input image, attach N style refs,
edit a saved prompt, run it through OpenAI gpt-image-1.5 or Google Gemini
3 Pro Image (Nano Banana Pro) on Vertex AI. UI at /imagegen.

Persisted on disk under ``imagegen/``:
  prompt.txt            — single saved prompt (editable)
  style_refs/{id}.png   — style reference library

The input image is per-request and is not stored.
"""

from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel


# ----- Config ----------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")

# Nano Banana Pro = gemini-3-pro-image-preview on Vertex AI. It is
# published in the `global` location (NOT us-central1, which is where
# Veo 3.1 lives), so we keep a dedicated GEMINI_LOCATION env var.
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview")
GEMINI_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GEMINI_LOCATION = os.getenv("GEMINI_LOCATION", "global")

APP_DIR = Path(__file__).resolve().parent
IG_DIR = APP_DIR / "imagegen"
IG_DIR.mkdir(exist_ok=True)
REFS_DIR = IG_DIR / "style_refs"
REFS_DIR.mkdir(exist_ok=True)
STATIC_DIR = APP_DIR / "static"

PROMPT_PATH = IG_DIR / "prompt.txt"

DEFAULT_PROMPT = (
    "Warm cozy semi-cartoon digital illustration portrait of a young Asian man "
    "at a scenic mountain overlook during golden hour, chest-up composition, "
    "soft cinematic sunset lighting, Zhangjiajie-style sandstone mountains in "
    "background, warm orange sunlight mixed with cool atmospheric haze, "
    "relaxed confident expression, detailed dark winter jacket with fur hood, "
    "leaning on rustic wooden railing, painterly stylized rendering, subtle "
    "pixel-art texture only, NOT heavily pixelated, clean attractive face, "
    "anime-inspired realism, soft shading, premium profile-picture composition, "
    "emotionally warm and aspirational, modern cozy illustration style, "
    "detailed environment but secondary to character, smooth facial rendering, "
    "gentle outlines, slightly cartoonized proportions, high visual clarity, "
    "beautiful lighting, social-media-worthy aesthetic"
)


def _read_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    return DEFAULT_PROMPT


def _list_refs() -> list[dict]:
    rows = []
    for p in sorted(REFS_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime):
        rows.append({
            "id": p.stem,
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })
    return rows


# ----- OpenAI gpt-image-1.5 --------------------------------------------------

def _openai_generate(prompt: str, input_png: bytes,
                     ref_pngs: list[bytes]) -> bytes:
    if not OPENAI_API_KEY:
        raise HTTPException(400, "OPENAI_API_KEY not configured on server")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = [("image[]", ("input.png", input_png, "image/png"))]
    for i, ref in enumerate(ref_pngs):
        files.append(("image[]", (f"ref{i}.png", ref, "image/png")))
    data = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "n": "1",
        "size": "1024x1024",
        "quality": "high",
        "output_format": "png",
    }
    try:
        r = requests.post(
            f"{OPENAI_BASE}/images/edits",
            headers=headers, data=data, files=files, timeout=600,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"OpenAI request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"OpenAI {r.status_code}: {r.text[:500]}")
    payload = r.json().get("data") or []
    if not payload or not payload[0].get("b64_json"):
        raise HTTPException(502, f"OpenAI returned no image: {r.text[:300]}")
    return base64.b64decode(payload[0]["b64_json"])


# ----- Gemini 3 Pro Image (Nano Banana Pro) on Vertex AI ---------------------

def _gemini_generate(prompt: str, input_png: bytes,
                     ref_pngs: list[bytes]) -> bytes:
    if not GEMINI_PROJECT:
        raise HTTPException(400, "GOOGLE_CLOUD_PROJECT not set on server")
    cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not cred or not os.path.isfile(cred):
        raise HTTPException(
            400, "GOOGLE_APPLICATION_CREDENTIALS not set or file missing")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise HTTPException(500, f"google-genai SDK not installed: {e}")

    client = genai.Client(
        vertexai=True, project=GEMINI_PROJECT, location=GEMINI_LOCATION,
    )

    # Build contents: text prompt first, then input image, then any refs.
    parts: list = [
        types.Part.from_text(text=prompt),
        types.Part.from_bytes(data=input_png, mime_type="image/png"),
    ]
    for ref in ref_pngs:
        parts.append(types.Part.from_bytes(data=ref, mime_type="image/png"))

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=parts,
            config=config,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Gemini request failed: {e}")

    # Walk candidates → parts → inline_data for the first image part.
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        cparts = getattr(content, "parts", None) or []
        for part in cparts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, str):
                    return base64.b64decode(data)
                return bytes(data)
    raise HTTPException(502, "Gemini returned no image")


# ----- Router ----------------------------------------------------------------

router = APIRouter()


def _auth(request: Request) -> None:
    from app import _require_auth  # noqa: WPS433
    _require_auth(request)


@router.get("/imagegen", response_class=HTMLResponse)
async def imagegen_page(request: Request) -> Response:
    from app import _is_authed  # noqa: WPS433
    if not _is_authed(request):
        from fastapi.responses import RedirectResponse  # noqa: WPS433
        return RedirectResponse("/")
    return FileResponse(STATIC_DIR / "imagegen.html")


@router.get("/api/imagegen/config")
async def imagegen_config(request: Request) -> JSONResponse:
    _auth(request)
    cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    return JSONResponse({
        "openai_configured": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_IMAGE_MODEL,
        "gemini_configured": bool(GEMINI_PROJECT and cred and os.path.isfile(cred)),
        "gemini_model": GEMINI_IMAGE_MODEL,
    })


@router.get("/api/imagegen/prompt")
async def imagegen_get_prompt(request: Request) -> JSONResponse:
    _auth(request)
    return JSONResponse({
        "prompt": _read_prompt(),
        "saved": PROMPT_PATH.exists(),
    })


class PromptBody(BaseModel):
    prompt: str


@router.post("/api/imagegen/prompt")
async def imagegen_save_prompt(body: PromptBody,
                               request: Request) -> JSONResponse:
    _auth(request)
    PROMPT_PATH.write_text(body.prompt)
    return JSONResponse({"ok": True})


@router.post("/api/imagegen/prompt/reset")
async def imagegen_reset_prompt(request: Request) -> JSONResponse:
    _auth(request)
    if PROMPT_PATH.exists():
        PROMPT_PATH.unlink()
    return JSONResponse({"ok": True, "prompt": DEFAULT_PROMPT})


@router.get("/api/imagegen/refs")
async def imagegen_list_refs(request: Request) -> JSONResponse:
    _auth(request)
    return JSONResponse({"refs": _list_refs()})


@router.post("/api/imagegen/refs")
async def imagegen_upload_ref(request: Request,
                              file: UploadFile = File(...)) -> JSONResponse:
    _auth(request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    ref_id = uuid.uuid4().hex[:12]
    (REFS_DIR / f"{ref_id}.png").write_bytes(data)
    return JSONResponse({"id": ref_id})


@router.get("/api/imagegen/refs/{ref_id}")
async def imagegen_get_ref(ref_id: str, request: Request) -> Response:
    _auth(request)
    p = REFS_DIR / f"{ref_id}.png"
    if not p.exists():
        raise HTTPException(404, "ref not found")
    return Response(content=p.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.delete("/api/imagegen/refs/{ref_id}")
async def imagegen_delete_ref(ref_id: str,
                              request: Request) -> JSONResponse:
    _auth(request)
    p = REFS_DIR / f"{ref_id}.png"
    if p.exists():
        p.unlink()
    return JSONResponse({"ok": True})


@router.post("/api/imagegen/generate")
async def imagegen_generate(request: Request,
                            file: UploadFile = File(...),
                            provider: str = Form(...),
                            prompt: str | None = Form(None)) -> Response:
    """Generate one image from `file` (input) + saved style refs + prompt.

    `provider` is either "openai" or "gemini". `prompt`, if given, overrides
    the saved prompt for THIS run only (does not auto-save).
    """
    _auth(request)
    input_png = await file.read()
    if not input_png:
        raise HTTPException(400, "empty input image")

    p_text = (prompt or "").strip() or _read_prompt()
    ref_pngs = [
        (REFS_DIR / f"{r['id']}.png").read_bytes() for r in _list_refs()
    ]

    t0 = time.time()
    if provider == "openai":
        out = _openai_generate(p_text, input_png, ref_pngs)
    elif provider == "gemini":
        out = _gemini_generate(p_text, input_png, ref_pngs)
    else:
        raise HTTPException(400, f"unknown provider: {provider}")
    elapsed = round(time.time() - t0, 1)

    return Response(content=out, media_type="image/png", headers={
        "Cache-Control": "no-store",
        "X-Elapsed": str(elapsed),
        "X-Provider": provider,
    })
