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

import gemini_client


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

DEFAULT_PROMPT = """\
Transform the uploaded photo into a cozy cinematic pixel-art portrait with a hand-crafted retro game aesthetic.

Style requirements:

Detailed pixel-art illustration, not realistic photography
Painterly pixel shading with visible pixel texture
Clean pixel clusters, intentional dithering, limited color palette
High-detail environment with layered depth
Cozy indie-game vibe similar to premium pixel RPG splash art
Character should remain recognizable while becoming slightly stylized/cartoonized
Smooth attractive face rendering, not overly blocky or noisy
Preserve the original pose, composition, expression, clothing, and camera angle
Rich atmospheric perspective and depth
Avoid flat pixel art or cheap retro filters
Avoid excessive realism
Avoid over-pixelation
Keep edges clean and aesthetically pleasing for profile-picture usage

Rendering characteristics:

Sharp silhouette
Soft cinematic contrast
Deep navy/bluish shadows
Amber/orange highlights
Slight glow from warm light sources
High visual clarity even at small size
Premium modern pixel-art game illustration quality

Composition rules:

Character occupies around 40–60% of frame
Slight zoom-in suitable for social media profile photos
Face must remain clear and readable
Maintain centered visual focus
Background supports the character instead of distracting from them

Negative prompts:

blurry
low detail
muddy colors
realistic skin texture
AI-looking face
distorted anatomy
extra fingers
noisy pixels
random artifacts
anime style
chibi
flat shading
vector art
3D render
oil painting
watercolor
oversaturated neon colors
cheap 8-bit filter
mosaic effect
low-resolution sprite look"""


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
    files = [("image[]", ("subject.png", input_png, "image/png"))]
    for i, ref in enumerate(ref_pngs):
        files.append(("image[]", (f"style_ref_{i+1}.png", ref, "image/png")))

    # gpt-image-1.5 /images/edits with multiple images treats them as an
    # unordered bag. The model has no built-in concept of "subject vs
    # style reference" — that distinction has to live in the PROMPT.
    # We prepend explicit role labels so the first image is treated as
    # the subject and the rest are read as style anchors.
    if ref_pngs:
        n = len(ref_pngs)
        labeled = (
            f"You are given {n + 1} attached images. The FIRST image "
            f"(subject.png) is the SUBJECT — keep its identity, pose, "
            f"and composition as the basis of the output. The next "
            f"{n} image{'s' if n != 1 else ''} (style_ref_1"
            + (f"…style_ref_{n}" if n > 1 else "")
            + ") are STYLE REFERENCES — match their art style, "
            "rendering, palette, line quality, texture, and overall "
            "aesthetic exactly. Do NOT copy their subject matter or "
            "composition; only their visual style.\n\n"
            "STYLE DESCRIPTION + EXTRA DIRECTION:\n"
        ) + prompt
    else:
        labeled = prompt

    data = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": labeled,
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
    # Developer API key path needs nothing else; the Vertex path still needs
    # a project + service-account JSON.
    if not gemini_client.using_api_key():
        if not GEMINI_PROJECT:
            raise HTTPException(
                400, "set GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT on server")
        cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if not cred or not os.path.isfile(cred):
            raise HTTPException(
                400, "set GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS")
    try:
        from google.genai import types
    except ImportError as e:
        raise HTTPException(500, f"google-genai SDK not installed: {e}")

    # API-key (Developer API) when GEMINI_API_KEY is set, else Vertex AI.
    client = gemini_client.new_client(GEMINI_PROJECT, GEMINI_LOCATION)

    # Gemini sees all attached images as an unlabeled set unless we
    # interleave text labels between them. Nano Banana Pro responds
    # well to explicit role labels — the recommended pattern is
    # text → image → text → image → … so the model can distinguish
    # subject from style references.
    parts: list = [
        types.Part.from_text(
            text=(
                "Task: regenerate the SUBJECT image below in the visual "
                "style of the STYLE REFERENCE image(s) that follow. "
                "Preserve the subject's identity, pose, and composition "
                "from the subject image. Match the art style, rendering, "
                "palette, line quality, texture, and overall aesthetic "
                "of the style references — do NOT copy their subject "
                "matter or composition."
            )
        ),
        types.Part.from_text(text="SUBJECT IMAGE:"),
        types.Part.from_bytes(data=input_png, mime_type="image/png"),
    ]
    if ref_pngs:
        parts.append(types.Part.from_text(
            text=f"STYLE REFERENCE IMAGE{'S' if len(ref_pngs) != 1 else ''} "
                 f"(match this aesthetic exactly):"
        ))
        for i, ref in enumerate(ref_pngs):
            if len(ref_pngs) > 1:
                parts.append(types.Part.from_text(
                    text=f"Style reference {i + 1} of {len(ref_pngs)}:"
                ))
            parts.append(types.Part.from_bytes(data=ref, mime_type="image/png"))
    parts.append(types.Part.from_text(
        text="STYLE DESCRIPTION + EXTRA DIRECTION:\n" + prompt
    ))

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
    gemini_ok = gemini_client.using_api_key() or bool(
        GEMINI_PROJECT and cred and os.path.isfile(cred))
    return JSONResponse({
        "openai_configured": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_IMAGE_MODEL,
        "gemini_configured": gemini_ok,
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
