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
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")

# Claude (Anthropic) is used for the "Suggest prompt" feature — it
# writes a fresh species/stage-specific image prompt in the user's
# established kawaii template. Image generation still uses OpenAI.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_BASE = os.getenv("ANTHROPIC_BASE", "https://api.anthropic.com/v1")

APP_DIR = Path(__file__).resolve().parent
FG_DIR = APP_DIR / "fishgen"
FG_DIR.mkdir(exist_ok=True)
STATIC_DIR = APP_DIR / "static"

STYLE_REF_PATH = FG_DIR / "style_ref.png"

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
    """Edit endpoint — used for teen/baby (evolve from previous stage)."""
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


def _openai_generate_with_style_ref(prompt: str, ref_png: bytes) -> bytes:
    """Generations endpoint with image as style reference — mirrors what the
    ChatGPT platform does when you attach an image and ask for a new image in
    that style. The image is a soft visual anchor, NOT an edit base."""
    if not OPENAI_API_KEY:
        raise HTTPException(400, "OPENAI_API_KEY not configured on server")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    # Pass as "image" (singular) — same as the platform's attach button.
    files = {"image": ("style_ref.png", ref_png, "image/png")}
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
            f"{OPENAI_BASE}/images/generations",
            headers=headers, data=data, files=files, timeout=300,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"OpenAI request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"OpenAI {r.status_code}: {r.text[:400]}")
    result = r.json().get("data") or []
    if not result or not result[0].get("b64_json"):
        raise HTTPException(502, f"OpenAI returned no image: {r.text[:200]}")
    return base64.b64decode(result[0]["b64_json"])


# ----- Prompt suggester (LLM) ------------------------------------------------

# Few-shot system prompt teaching the model the exact template the
# user wants. Three koi examples (adult, teen, baby) cover the full
# stage chain; one blobfish adult adds variety so the model doesn't
# overfit to "koi-shaped" results.
_SUGGEST_SYSTEM = """\
You write image-generation prompts for a series of kawaii cartoon fish illustrations. The artist will pass your prompt to gpt-image-1, which will ALWAYS receive an attached reference image. You must follow the exact template, structure, and tone shown in the examples below — DO NOT add any prose around the prompt, DO NOT include markdown code fences, just output the prompt itself.

ART STYLE (always): soft watercolour cartoon rendering, kawaii face (large eye, pink cheek circle, tiny smile), flat 2D side-view, body facing LEFT, fully transparent PNG background, no sheen, no 3D, no realism.

THREE STAGES per species — every stage uses an attached reference image:
- ADULT: full mature form. All distinguishing features present. The attached image is a global kawaii cartoon style reference. Opening line says "in the EXACT SAME art style as the attached reference image — same soft watercolour cartoon rendering, same outline thickness, same watercolour texture, same kawaii eye / pink cheek / small smile face treatment, same canvas resolution, same polish level."
- TEEN: a younger version of the adult. The attached image is the saved ADULT sprite. Opening line says "in the EXACT SAME art style as the attached reference image" and a second paragraph explicitly references the adult sprite as the colour/style anchor. Simpler patterns, less developed fins, simpler tail.
- BABY: simplest form. The attached image is the saved TEEN sprite. Opens the same way, second paragraph references the teen. Plain solid base colour, tiny fins, no markings yet.

EVERY prompt MUST contain these sections, in this exact order, with these exact section headers:

1. Opening paragraph (no header). Opens with "A single kawaii cartoon {stage} {species}, in the EXACT SAME art style as the attached reference image — same soft watercolour cartoon rendering, same outline thickness, same watercolour texture, same kawaii eye / pink cheek / small smile face treatment, same canvas resolution, same polish level. Flat 2D side-view illustration, body facing LEFT. Fully transparent PNG background."
   For TEEN and BABY only: add a second sentence/paragraph explicitly naming the previous-stage sprite as the colour/rendering anchor (e.g. "Match the art style and colour treatment of the provided adult {species} exactly — same rendering quality, same [base colour]. But follow the shape and pattern description below independently.").

2. THE UNLOCK FEELING: One short paragraph evoking what makes this stage's silhouette distinct. Use plain language, hint at the species' identity and what about it is "almost there" or "fully there" or "not there yet" depending on stage.

3. SILHOUETTE:
   • Bullet points describing body shape (with width-to-height ratio for adult/teen), head, fins, tail, distinguishing features specific to this species (e.g. koi has barbels, blobfish has droopy nose, axolotl has gill stalks).

4. PALETTE:
   • Base colour with a real-life-accurate hex code in parens (e.g. "#D94F2A range").
   • Pattern colours and where they appear.
   • Fin colour.
   • Outline colour.
   • End with "NO highlight, NO sheen. Flat watercolour only." (or similar).

5. FACE:
   • Bullet describing the kawaii face treatment for this stage.

6. GUARDRAILS:
   • Bullet points enforcing the most important constraints — what MUST be present, what MUST NOT. Include "NO sheen or shine." and "FLAT 2D cartoon — no 3D, no realism, no photograph." as last two items.

EXAMPLES:

=== ADULT KOI ===
A single kawaii cartoon adult koi fish, in the EXACT SAME art style as the attached reference image — same soft watercolour cartoon rendering, same outline thickness, same watercolour texture, same kawaii eye / pink cheek / small smile face treatment, same canvas resolution, same polish level. Flat 2D side-view illustration, body facing LEFT. Fully transparent PNG background.

THE UNLOCK FEELING: A beautiful koi — deep red-orange, white patches, and a few bold black marks. Clean and charming. Unmistakably a koi.

SILHOUETTE:
• Plump rounded body — width-to-height ~1.8 : 1. Chubby and soft.
• Head broad and soft with a small rounded mouth.
• Two small short barbels at the mouth — subtle, not dramatic.
• Dorsal fin gently raised along the back — soft and rounded.
• TAIL FIN: a wide soft fan — both lobes rounded and spread open.
• No visible scale texture — smooth and clean.

PALETTE:
• Base: deep red-orange — closer to vermillion than tangerine. A rich, warm red-orange (#D94F2A range). This is the real koi colour — deeper and more red than a typical cartoon orange.
• Clean white patches — 2 to 3 simple organic brushstroke shapes on the body.
• 2 to 3 small black markings — simple bold spots or short stripes on the orange areas only, not over the white.
• Fins: translucent warm red-orange.
• Outline: deep rust-brown.
• NO highlight, NO sheen, NO reflection. Flat watercolour only.

FACE:
• Large kawaii eye, pink cheek circle, tiny upturned smile.

GUARDRAILS:
• The red-orange must be clearly deeper and more red than a standard cartoon orange. Think real koi pond colour, not tangerine.
• Black markings present but restrained — 2 to 3 spots max. Cute, not busy.
• Tail is a wide soft fan.
• NO sheen or shine.
• FLAT 2D cartoon — no 3D, no realism, no photograph.

=== TEEN KOI ===
A single kawaii cartoon teen koi fish, in the EXACT SAME art style as the attached reference image — same soft watercolour cartoon rendering, same outline thickness, same watercolour texture, same kawaii face treatment, same canvas resolution. Flat 2D side-view illustration, body facing LEFT. Fully transparent PNG background.

Match the art style and colour treatment of the provided adult koi exactly — same rendering quality, same deep red-orange base colour. But follow the shape and pattern description below independently.

THE UNLOCK FEELING: White patches are just appearing for the first time. No black yet. The tail is a simple fork. A young koi whose pattern is only beginning.

SILHOUETTE:
• Same plump rounded body as adult, slightly rounder.
• Small rounded mouth, no barbels yet.
• Dorsal fin small and low.
• TAIL FIN: a simple soft fork — two narrow lobes. Not spread, not flowing. Structurally much simpler than the adult's wide fan.

PALETTE:
• Same deep red-orange as adult.
• 1 to 2 small white patches — sparse, just beginning to appear.
• NO black markings — those come with the adult.
• Fins: translucent warm red-orange.
• Outline: deep rust-brown.
• NO highlight, NO sheen. Flat watercolour only.

FACE:
• Same kawaii treatment as adult.

GUARDRAILS:
• NO black markings — orange and white only.
• Tail is a SIMPLE FORK — not a fan.
• Only 1–2 white patches — sparse.
• No barbels.
• NO sheen or shine.
• FLAT 2D cartoon — no 3D, no realism, no photograph.

=== BABY KOI ===
A single kawaii cartoon baby koi fish, in the EXACT SAME art style as the attached reference image — same soft watercolour cartoon rendering, same outline thickness, same watercolour texture, same kawaii face treatment, same canvas resolution. Flat 2D side-view illustration, body facing LEFT. Fully transparent PNG background.

Match the art style and colour treatment of the provided teen koi exactly — same rendering quality, same deep red-orange base colour. But follow the shape and pattern description below independently.

THE UNLOCK FEELING: Pure solid red-orange. No markings at all. A chubby little koi in one warm colour — simple and irresistible.

SILHOUETTE:
• Round chubby body — width-to-height ~1.3 : 1. Plump and soft, slightly longer than a circle but very round.
• Head large and soft, tiny rounded mouth.
• No barbels — completely absent.
• Dorsal fin: a tiny soft nub on top.
• TAIL FIN: a small soft rounded petal — not forked.
• Smooth skin — no texture.

PALETTE:
• SOLID deep red-orange across the entire body — same colour as teen. No white patches, no black. Single colour only.
• Fins: translucent warm red-orange.
• Outline: deep rust-brown.
• NO highlight, NO sheen. Flat watercolour only.

FACE:
• Same kawaii treatment — large eye, pink cheek, tiny upturned smile.

GUARDRAILS:
• ONE colour — solid red-orange. No markings whatsoever.
• No barbels. Tail is a soft rounded petal.
• Chubby koi shape — not a perfect circle.
• NO sheen or shine.
• FLAT 2D cartoon — no 3D, no realism, no photograph.

=== ADULT BLOBFISH (different species, shows variety) ===
A single kawaii cartoon adult blobfish, in the EXACT SAME art style as the attached reference image — same soft watercolour cartoon rendering, same outline thickness, same watercolour texture, same kawaii eye / pink cheek face treatment, same canvas resolution, same polish level. Flat 2D side-view illustration, body facing LEFT. Fully transparent PNG background.

THE UNLOCK FEELING: The iconic blobfish — droopy nose, sad eyes, pink blob body. Instantly recognisable. Ugly-cute in the most charming way possible.

SILHOUETTE:
• Round soft blob body — slightly wider than tall. Gelatinous and squishy-looking.
• THE DROOPY NOSE: a large soft fleshy bulge that droops downward from the front of the face — this is the blobfish's entire identity and must be prominent.
• Cheeks soft and heavy, giving the face a jowly quality.
• Two tiny stubby fins on the sides.
• Small soft tail fin.

PALETTE:
• Soft muted pink-grey (#D4A5A0 range) — pale and matte.
• Slightly paler on the underside.
• Outline: deep grey-mauve.
• NO highlight, NO sheen, NO reflection. Flat watercolour only.

FACE:
• Large kawaii eyes — bright with a white highlight dot, but with heavy soft eyelids giving a permanently drowsy/melancholy look.
• Pink cheek circles — kawaii treatment applied to a sad face.
• Tiny downturned or neutral mouth just above the drooping nose.

GUARDRAILS:
• The DROOPY NOSE must be large and prominent — this is the blobfish.
• Eyes sad but kawaii — heavy-lidded, large, with highlight dot.
• Body soft pink-grey blob.
• NO sheen or shine.
• FLAT 2D cartoon — no 3D, no realism, no photograph.

=== END EXAMPLES ===

When the user asks for a {stage} prompt for a {species}, write a single prompt for that stage that follows the template exactly, with species-specific silhouette details (research what makes that fish recognisable — e.g. seahorse curl, hammerhead's wide T-head, swordfish's bill, manta ray's flat triangular wings, octopus's eight tentacles). Use a real-life-accurate base colour with hex code. Pick the distinguishing features that MUST be present and put them in GUARDRAILS.

Output ONLY the prompt — no preface, no closing remarks, no markdown.
"""


def _claude_suggest_prompt(species: str, stage: str,
                           parent_prompt: str | None = None) -> str:
    """Call Anthropic's Messages API to draft a fresh image prompt.

    parent_prompt: the saved prompt from the previous stage (adult for teen,
    teen for baby). When provided, Claude uses it as the concrete colour/
    silhouette anchor instead of inventing one from scratch.
    """
    if not ANTHROPIC_API_KEY:
        raise HTTPException(
            400, "ANTHROPIC_API_KEY not configured on server")

    if parent_prompt:
        parent_stage = "adult" if stage == "teen" else "teen"
        user_content = (
            f"Write a {stage} prompt for {species}.\n\n"
            f"Here is the actual {parent_stage} prompt that was used — "
            f"match its colours, outline style, and silhouette decisions "
            f"exactly, then simplify for the {stage} stage:\n\n"
            f"{parent_prompt}"
        )
    else:
        user_content = f"Write a {stage} prompt for {species}."

    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "temperature": 0.7,
        "system": _SUGGEST_SYSTEM,
        "messages": [
            {"role": "user", "content": user_content},
        ],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        r = requests.post(
            f"{ANTHROPIC_BASE}/messages",
            headers=headers, json=body, timeout=120,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Anthropic request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(
            502, f"Anthropic {r.status_code}: {r.text[:400]}")
    j = r.json()
    blocks = j.get("content") or []
    text = ""
    for b in blocks:
        if b.get("type") == "text":
            text += b.get("text", "")
    text = text.strip()
    if not text:
        raise HTTPException(
            502, f"Anthropic returned no text: {r.text[:200]}")
    # Strip accidental code-fence wrappers if the model adds them.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    return text


_REFINE_SYSTEM = """\
You are refining image-generation prompts for a series of kawaii cartoon fish illustrations. The user has a current prompt and is giving you feedback about what was wrong with the generated image (e.g. "too round", "fins too small", "colour too orange").

Your job: return a revised version of the prompt that addresses the feedback while keeping everything else the same — same template structure (opening, THE UNLOCK FEELING, SILHOUETTE, PALETTE, FACE, GUARDRAILS), same art style requirements, same kawaii treatment.

Rules:
- Output ONLY the revised prompt. No preamble, no explanation, no markdown fences.
- Keep every section that did not need changing exactly as it was.
- Make targeted edits only — do not rewrite sections that were not mentioned in the feedback.
- If the feedback is about shape (e.g. "too round"), adjust the SILHOUETTE section and add a GUARDRAIL.
- If the feedback is about colour, adjust the PALETTE section.
- If the feedback is about a specific feature being wrong, fix that bullet and reinforce it in GUARDRAILS.
"""


def _claude_refine_prompt(history: list[dict]) -> str:
    """Call Anthropic with a multi-turn conversation to refine a prompt."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured on server")
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "temperature": 0.5,
        "system": _REFINE_SYSTEM,
        "messages": history,
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        r = requests.post(
            f"{ANTHROPIC_BASE}/messages",
            headers=headers, json=body, timeout=120,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Anthropic request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"Anthropic {r.status_code}: {r.text[:400]}")
    j = r.json()
    blocks = j.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not text:
        raise HTTPException(502, f"Anthropic returned no text: {r.text[:200]}")
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    return text


# ----- Auth bridge -----------------------------------------------------------

def _require_auth(request: Request) -> None:
    from app import _require_fish_auth as _ra  # noqa: WPS433
    _ra(request)


def _is_authed(request: Request) -> bool:
    from app import _is_fish_authed as _ia  # noqa: WPS433
    return _ia(request)


# ----- Router + routes -------------------------------------------------------

router = APIRouter()


@router.get("/fishgen", response_class=HTMLResponse)
async def fishgen_page(request: Request) -> Response:
    if not _is_authed(request):
        from fastapi.responses import RedirectResponse  # noqa: WPS433
        return RedirectResponse("/?tab=fish")
    return FileResponse(STATIC_DIR / "fishgen.html")


@router.get("/api/fishgen/list")
async def fishgen_list(request: Request) -> JSONResponse:
    _require_auth(request)
    # Lazy-read PIXELLAB_SECRET each call so a server-side env update
    # is reflected without restart.
    from app import PIXELLAB_SECRET, _FS_ACTION  # noqa: WPS433
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
        "anthropic_configured": bool(ANTHROPIC_API_KEY),
        "anthropic_model": ANTHROPIC_MODEL,
        "has_style_ref": STYLE_REF_PATH.exists(),
        "default_animate_prompt": _FS_ACTION,
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


@router.get("/api/fishgen/style_ref")
async def fishgen_style_ref_get(request: Request) -> Response:
    _require_auth(request)
    if not STYLE_REF_PATH.exists():
        raise HTTPException(404, "no style reference image uploaded yet")
    return Response(content=STYLE_REF_PATH.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/fishgen/style_ref")
async def fishgen_style_ref_upload(request: Request) -> JSONResponse:
    from fastapi import UploadFile, File  # noqa: WPS433
    _require_auth(request)
    body = await request.body()
    if not body:
        raise HTTPException(400, "no image data")
    STYLE_REF_PATH.write_bytes(body)
    return JSONResponse({"ok": True})


@router.delete("/api/fishgen/style_ref")
async def fishgen_style_ref_delete(request: Request) -> JSONResponse:
    _require_auth(request)
    try:
        STYLE_REF_PATH.unlink()
    except FileNotFoundError:
        pass
    return JSONResponse({"ok": True})


@router.post("/api/fishgen/{slug}/{stage}/suggest_prompt")
async def fishgen_suggest_prompt(slug: str, stage: str,
                                 request: Request) -> JSONResponse:
    """Use Claude (Anthropic) to draft a fresh prompt for this species
    + stage, following the established template + few-shot examples.
    The result is returned but NOT auto-saved — the user reviews +
    edits in the textarea, then saves manually."""
    _require_auth(request)
    if slug not in SLUG_TO_NAME:
        raise HTTPException(404, f"unknown species: {slug}")
    if stage not in STAGE_KEYS:
        raise HTTPException(404, f"unknown stage: {stage}")
    species_name = SLUG_TO_NAME[slug]
    parent_prompt: str | None = None
    if stage == "teen":
        p = FG_DIR / slug / "adult" / "prompt.txt"
        if p.exists():
            parent_prompt = p.read_text().strip() or None
    elif stage == "baby":
        p = FG_DIR / slug / "teen" / "prompt.txt"
        if p.exists():
            parent_prompt = p.read_text().strip() or None
    prompt = _claude_suggest_prompt(species_name, stage, parent_prompt)
    return JSONResponse({"prompt": prompt, "model": ANTHROPIC_MODEL})


class RefineBody(BaseModel):
    history: list[dict]  # [{role: "user"|"assistant", content: str}]


@router.post("/api/fishgen/{slug}/{stage}/refine_prompt")
async def fishgen_refine_prompt(slug: str, stage: str, body: RefineBody,
                                request: Request) -> JSONResponse:
    """Refine an existing prompt based on user feedback. The client sends
    the full conversation history; Claude returns an improved prompt."""
    _require_auth(request)
    if slug not in SLUG_TO_NAME:
        raise HTTPException(404, f"unknown species: {slug}")
    if stage not in STAGE_KEYS:
        raise HTTPException(404, f"unknown stage: {stage}")
    if not body.history:
        raise HTTPException(400, "history must not be empty")
    prompt = _claude_refine_prompt(body.history)
    return JSONResponse({"prompt": prompt, "model": ANTHROPIC_MODEL})


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


@router.post("/api/fishgen/{slug}/{stage}/upload")
async def fishgen_upload(slug: str, stage: str,
                         request: Request) -> JSONResponse:
    """Accept a user-supplied PNG (generated on the OpenAI platform or
    elsewhere) and save it as the still image for this cell."""
    _require_auth(request)
    d = _stage_dir(slug, stage)
    body = await request.body()
    if not body:
        raise HTTPException(400, "no image data")
    (d / "image.png").write_bytes(body)
    for fn in ("sheet.png", "sheet_meta.json"):
        try:
            (d / fn).unlink()
        except FileNotFoundError:
            pass
    return JSONResponse({"ok": True, "meta": _stage_meta(slug, stage)})


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
        # Adult: use generations+image[] (soft style reference) if uploaded,
        # otherwise pure text-to-image. Never use the edit endpoint for adults.
        if STYLE_REF_PATH.exists():
            png_bytes = _openai_generate_with_style_ref(body.prompt, STYLE_REF_PATH.read_bytes())
        else:
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


class AnimateBody(BaseModel):
    action: str = ""  # empty = use server default _FS_ACTION


@router.post("/api/fishgen/{slug}/{stage}/animate")
async def fishgen_animate(slug: str, stage: str, body: AnimateBody,
                          request: Request) -> JSONResponse:
    """Generate the swim-in-place sprite sheet from the saved still
    image. Reuses the existing PixelLab pipeline (`_generate_one` from
    app.py). Synchronous — returns when the sheet is written."""
    _require_auth(request)
    from app import (  # noqa: WPS433
        PIXELLAB_SECRET,
        _generate_one,
    )
    if not PIXELLAB_SECRET:
        raise HTTPException(400, "PIXELLAB_SECRET not configured on server")
    d = _stage_dir(slug, stage)
    ref_path = d / "image.png"
    if not ref_path.exists():
        raise HTTPException(400, "upload an image first")
    action = body.action.strip() or None
    sheet_bytes, n_frames, frame_w = _generate_one(ref_path.read_bytes(), action=action)
    (d / "sheet.png").write_bytes(sheet_bytes)
    (d / "sheet_meta.json").write_text(json.dumps({
        "frames": n_frames,
        "frameW": frame_w,
        "frameH": frame_w,
        "created_at": time.time(),
    }))
    return JSONResponse({
        "ok": True,
        "frames": n_frames,
        "frameW": frame_w,
        "frameH": frame_w,
        "meta": _stage_meta(slug, stage),
    })
