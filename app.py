"""FastAPI app: password-gated UI for generating HSK TTS.

Endpoints:
- GET  /                    → login or main UI
- POST /login               → set session cookie
- POST /logout
- POST /api/words           → start a "words → MP3 folder" job (txt upload)
- POST /api/exam            → start a "HSK exam → audio pack" job (xlsx upload)
- GET  /api/jobs/{job_id}   → job progress JSON
- GET  /api/jobs/{job_id}/download → download zip when ready
"""

import base64
import http.client
import io
import json
import os
import random
import re
import secrets
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel

import db
from excel_parser import parse_instructions, parse_questions
from pinyin_util import annotate_with_pinyin, pinyin_options
from tts import FEMALE_VOICES, MALE_VOICES, MODEL_DEFAULT, TTSError, build_segment, synthesize_piece_mp3

APP_PASSWORD = os.getenv("APP_PASSWORD", "chinesely")
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))
SESSION_COOKIE = "chinesely_session"
serializer = URLSafeSerializer(SESSION_SECRET, salt="auth")

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="Chinesely TTS")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ----- auth helpers -----

def _is_authed(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        data = serializer.loads(token)
    except BadSignature:
        return False
    return isinstance(data, dict) and data.get("ok") is True


def _require_auth(request: Request) -> None:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="not authenticated")


# ----- job tracking -----

@dataclass
class Job:
    id: str
    kind: str                                # "words" | "exam"
    total: int = 0
    done: int = 0
    status: str = "pending"                  # pending | running | done | error
    error: str | None = None
    result_bytes: bytes | None = None
    result_filename: str = "output.zip"
    started_at: float = field(default_factory=time.time)
    current: str = ""                        # last thing being processed (for UI)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _get_job(job_id: str) -> Job:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def _prune_old_jobs(max_age_seconds: int = 3600) -> None:
    cutoff = time.time() - max_age_seconds
    with JOBS_LOCK:
        for jid in [j.id for j in JOBS.values() if j.started_at < cutoff]:
            JOBS.pop(jid, None)


# ----- routes -----

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    if not _is_authed(request):
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/login")
async def login(password: str = Form(...)) -> Response:
    if password != APP_PASSWORD:
        return RedirectResponse(url="/?error=1", status_code=303)
    token = serializer.dumps({"ok": True})
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@app.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/defaults")
async def defaults(request: Request) -> JSONResponse:
    _require_auth(request)
    return JSONResponse({
        "female_voices": FEMALE_VOICES,
        "male_voices": MALE_VOICES,
        "model": MODEL_DEFAULT,
        "has_api_key": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()),
    })


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> JSONResponse:
    _require_auth(request)
    job = _get_job(job_id)
    return JSONResponse({
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "total": job.total,
        "done": job.done,
        "current": job.current,
        "error": job.error,
        "filename": job.result_filename,
    })


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str, request: Request) -> Response:
    _require_auth(request)
    job = _get_job(job_id)
    if job.status != "done" or not job.result_bytes:
        raise HTTPException(status_code=409, detail="job not ready")
    return Response(
        content=job.result_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job.result_filename}"'},
    )


# ----- utils -----

SAFE_FN_RE = re.compile(r'[\\/:*?"<>|\0]')


def _safe_filename(s: str, max_len: int = 80) -> str:
    s = SAFE_FN_RE.sub("_", s).strip()
    return s[:max_len] or "untitled"


def _resolve_api_key(user_key: str | None) -> str:
    key = (user_key or "").strip() or os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise TTSError("No DASHSCOPE_API_KEY provided (server has no default)")
    return key


# ----- word-list job -----

@app.post("/api/words")
async def start_words_job(
    request: Request,
    file: UploadFile = File(...),
    voice: str = Form("Serena"),
    model: str = Form(MODEL_DEFAULT),
    speed: float = Form(1.0),
    tail_pad: float = Form(0.3),
    throttle_seconds: float = Form(1.0),
    api_key: str = Form(""),
) -> JSONResponse:
    _require_auth(request)
    _prune_old_jobs()

    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    words = []
    seen = set()
    for line in raw.splitlines():
        w = line.strip()
        if not w or w in seen:
            continue
        seen.add(w)
        words.append(w)
    if not words:
        raise HTTPException(status_code=400, detail="no words in file")

    job = Job(id=uuid.uuid4().hex, kind="words", total=len(words))
    job.result_filename = f"{Path(file.filename or 'words').stem}_mp3.zip"
    with JOBS_LOCK:
        JOBS[job.id] = job

    try:
        key = _resolve_api_key(api_key)
    except TTSError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def worker() -> None:
        job.status = "running"
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for word in words:
                    job.current = word
                    try:
                        mp3 = build_segment(
                            pieces=[(word, voice)],
                            model=model,
                            api_key=key,
                            speed=speed,
                            gap_between_speakers=0,
                            repeat=1,
                            tail_pad=tail_pad,
                        )
                        zf.writestr(f"{_safe_filename(word)}.mp3", mp3)
                    except Exception as e:
                        zf.writestr(
                            f"_errors/{_safe_filename(word)}.txt",
                            f"Failed: {e}\n",
                        )
                    job.done += 1
                    if throttle_seconds > 0:
                        time.sleep(throttle_seconds)
            job.result_bytes = buf.getvalue()
            job.status = "done"
            job.current = ""
        except Exception as e:
            job.status = "error"
            job.error = str(e)

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse({"job_id": job.id})


# ----- HSK browser (MongoDB) -----


class WordEditBody(BaseModel):
    pinyin: str | None = None
    english: str | None = None


class RegenBody(BaseModel):
    pinyin: str | None = None   # optional override: saved + used as hint for TTS
    voice: str = "Serena"
    model: str = MODEL_DEFAULT
    speed: float = 1.0
    tail_pad: float = 0.3
    api_key: str = ""


@app.get("/api/hsk/levels")
async def hsk_levels(request: Request) -> JSONResponse:
    _require_auth(request)
    return JSONResponse({"levels": db.list_levels()})


@app.get("/api/hsk/lessons")
async def hsk_lessons(level: str, request: Request) -> JSONResponse:
    _require_auth(request)
    if level not in db.list_levels():
        raise HTTPException(status_code=400, detail="level must be 1–6")
    try:
        lessons = db.list_lessons(level)
    except db.DBError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse({"lessons": lessons})


@app.get("/api/hsk/lessons/{lesson_id}/words")
async def hsk_lesson_words(lesson_id: str, request: Request) -> JSONResponse:
    _require_auth(request)
    try:
        data = db.get_lesson_words(lesson_id)
    except db.DBError as e:
        raise HTTPException(status_code=404, detail=str(e))
    for w in data["words"]:
        w["pinyinOptions"] = pinyin_options(w["chinese"], w["pinyin"])
    return JSONResponse(data)


@app.get("/api/hsk/words/{word_id}/audio")
async def hsk_word_audio(word_id: str, request: Request) -> Response:
    _require_auth(request)
    try:
        audio = db.get_word_audio(word_id)
    except db.DBError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not audio:
        raise HTTPException(status_code=404, detail="no audio for this word")
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.patch("/api/hsk/words/{word_id}")
async def hsk_word_edit(
    word_id: str,
    body: WordEditBody,
    request: Request,
) -> JSONResponse:
    _require_auth(request)
    try:
        updated = db.update_word_fields(
            word_id,
            pinyin=body.pinyin,
            english=body.english,
        )
    except db.DBError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(updated)


@app.post("/api/hsk/words/{word_id}/regenerate")
async def hsk_word_regenerate(
    word_id: str,
    body: RegenBody,
    request: Request,
) -> JSONResponse:
    _require_auth(request)
    try:
        key = _resolve_api_key(body.api_key)
    except TTSError as e:
        raise HTTPException(status_code=400, detail=str(e))

    word = db.get_word(word_id)
    if not word or not word["chinese"]:
        raise HTTPException(status_code=404, detail="word not found")

    # Save pinyin override (if provided) before regenerating so the DB reflects
    # the user's intent even if TTS fails midway.
    pinyin_for_tts = word["pinyin"]
    if body.pinyin is not None and body.pinyin.strip():
        pinyin_for_tts = body.pinyin.strip()
        db.update_word_fields(word_id, pinyin=pinyin_for_tts)

    text = annotate_with_pinyin(word["chinese"], pinyin_for_tts) if pinyin_for_tts else word["chinese"]

    try:
        mp3 = synthesize_piece_mp3(
            text=text,
            voice=body.voice,
            model=body.model,
            api_key=key,
            speed=body.speed,
            tail_pad_seconds=body.tail_pad,
        )
    except TTSError as e:
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

    try:
        db.update_word_audio(word_id, mp3)
    except db.DBError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse({
        "ok": True,
        "bytes": len(mp3),
        "pinyin": pinyin_for_tts,
    })


# ----- exam job -----

@app.post("/api/exam")
async def start_exam_job(
    request: Request,
    file: UploadFile = File(...),
    female_voice: str = Form("Serena"),
    male_voice: str = Form("Ethan"),
    model: str = Form(MODEL_DEFAULT),
    speed: float = Form(1.0),
    tail_pad: float = Form(0.3),
    gap_between_speakers: float = Form(0.6),
    gap_between_repeats: float = Form(1.0),
    part12_repeat: int = Form(2),
    part3_repeat: int = Form(2),
    part4_repeat: int = Form(1),
    throttle_seconds: float = Form(1.0),
    include_instructions: bool = Form(True),
    api_key: str = Form(""),
) -> JSONResponse:
    _require_auth(request)
    _prune_old_jobs()

    # Save xlsx to a temp file for openpyxl
    raw = await file.read()
    tmp = APP_DIR / f"_upload_{uuid.uuid4().hex}.xlsx"
    tmp.write_bytes(raw)
    try:
        questions = parse_questions(tmp)
        instructions = parse_instructions(tmp) if include_instructions else []
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Excel parse error: {e}")
    tmp.unlink(missing_ok=True)

    if not questions:
        raise HTTPException(status_code=400, detail="no questions found in Excel")

    total = len(questions) + len(instructions)
    job = Job(id=uuid.uuid4().hex, kind="exam", total=total)
    job.result_filename = f"{Path(file.filename or 'hsk').stem}_audio.zip"
    with JOBS_LOCK:
        JOBS[job.id] = job

    try:
        key = _resolve_api_key(api_key)
    except TTSError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def worker() -> None:
        job.status = "running"
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for q in questions:
                    label = f"{q.paper} / Q{q.number:02d} (Part {q.part})"
                    job.current = label
                    try:
                        if q.part in (1, 2):
                            # Repeat the same sentence with female then male alternation
                            pieces = [(q.text_a, female_voice), (q.text_a, male_voice)]
                            # Each voice once per "run", repeat = 1 (two pieces already cover the 2x).
                            # Map user's "part12_repeat" setting by chaining pieces.
                            # If user wants 2 reads (default), pieces as-is gives female+male.
                            # If they want e.g. 4 reads, extend.
                            extra_reads = max(1, part12_repeat) - 2
                            for i in range(extra_reads):
                                pieces.append((q.text_a, female_voice if i % 2 == 0 else male_voice))
                            mp3 = build_segment(
                                pieces=pieces,
                                model=model,
                                api_key=key,
                                speed=speed,
                                gap_between_speakers=gap_between_speakers,
                                repeat=1,
                                gap_between_repeats=gap_between_repeats,
                                tail_pad=tail_pad,
                            )
                        elif q.part == 3:
                            pieces = [(q.text_a, female_voice), (q.text_b, male_voice)]
                            mp3 = build_segment(
                                pieces=pieces,
                                model=model,
                                api_key=key,
                                speed=speed,
                                gap_between_speakers=gap_between_speakers,
                                repeat=max(1, part3_repeat),
                                gap_between_repeats=gap_between_repeats,
                                tail_pad=tail_pad,
                            )
                        else:  # part 4
                            pieces = [(q.text_a, male_voice), (q.text_b, female_voice)]
                            mp3 = build_segment(
                                pieces=pieces,
                                model=model,
                                api_key=key,
                                speed=speed,
                                gap_between_speakers=gap_between_speakers,
                                repeat=max(1, part4_repeat),
                                gap_between_repeats=gap_between_repeats,
                                tail_pad=tail_pad,
                            )
                        folder = f"{_safe_filename(q.paper)}/Part{q.part}"
                        filename = f"Q{q.number:02d}.mp3"
                        zf.writestr(f"{folder}/{filename}", mp3)
                    except Exception as e:
                        zf.writestr(
                            f"_errors/{_safe_filename(q.paper)}_Q{q.number:02d}.txt",
                            f"Failed: {e}\n",
                        )
                    job.done += 1
                    if throttle_seconds > 0:
                        time.sleep(throttle_seconds)

                for ins in instructions:
                    job.current = f"Instruction {ins.index}"
                    try:
                        mp3 = build_segment(
                            pieces=[(ins.text, female_voice)],
                            model=model,
                            api_key=key,
                            speed=speed,
                            gap_between_speakers=0,
                            repeat=1,
                            tail_pad=tail_pad,
                        )
                        zf.writestr(f"Instructions/I{ins.index:02d}.mp3", mp3)
                    except Exception as e:
                        zf.writestr(f"_errors/Instruction_{ins.index:02d}.txt", f"Failed: {e}\n")
                    job.done += 1
                    if throttle_seconds > 0:
                        time.sleep(throttle_seconds)

            job.result_bytes = buf.getvalue()
            job.status = "done"
            job.current = ""
        except Exception as e:
            job.status = "error"
            job.error = str(e)

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse({"job_id": job.id})


# ============================================================
# Fish Studio — sprite generation via PixelLab
# ============================================================

try:
    from PIL import Image as _PILImage
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

PIXELLAB_SECRET = os.getenv("PIXELLAB_SECRET", "")
SPRITES_DIR = APP_DIR / "sprites"
SPRITES_DIR.mkdir(exist_ok=True)

_FS_FRAME  = 256
_FS_FRAMES = 8
_FS_GENS   = 3
_FS_ACTION = (
    "the fish swims forward, side view, tail and fins gently undulating, "
    "eyes not moving. no color change. the body color stays exactly the same. "
    "no blink. no shadow."
)


def _pl_headers() -> dict:
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {PIXELLAB_SECRET}"}


def _pl_poll(job_id: str) -> dict:
    deadline = time.time() + 360
    while time.time() < deadline:
        conn = http.client.HTTPSConnection("api.pixellab.ai")
        conn.request("GET", f"/v2/background-jobs/{job_id}", headers=_pl_headers())
        res = conn.getresponse(); body = res.read(); conn.close()
        if res.status != 200:
            raise RuntimeError(f"poll {res.status}: {body.decode()[:200]}")
        pl = json.loads(body)
        st = pl.get("status")
        if st == "completed":
            return pl.get("last_response") or pl
        if st in ("failed", "error", "cancelled"):
            raise RuntimeError(f"PixelLab job {job_id} {st}")
        time.sleep(3)
    raise RuntimeError("PixelLab job timed out")


def _generate_one(ref_png: bytes) -> tuple[bytes, int]:
    if not _PIL_OK:
        raise RuntimeError("Pillow not installed — pip install Pillow")

    img = _PILImage.open(io.BytesIO(ref_png)).convert("RGBA")
    if img.size != (_FS_FRAME, _FS_FRAME):
        img = img.resize((_FS_FRAME, _FS_FRAME), _PILImage.NEAREST)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    ref_b64 = {"type": "base64",
                "base64": f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}",
                "format": "png"}

    payload = json.dumps({
        "first_frame":   ref_b64,
        "last_frame":    ref_b64,
        "action":        _FS_ACTION,
        "frame_count":   _FS_FRAMES,
        "no_background": True,
        "seed":          random.randint(1, 2 ** 31 - 1),
    })
    conn = http.client.HTTPSConnection("api.pixellab.ai")
    conn.request("POST", "/v2/animate-with-text-v3", payload, _pl_headers())
    res = conn.getresponse(); body = res.read(); conn.close()
    if res.status not in (200, 202):
        raise RuntimeError(f"PixelLab {res.status}: {body.decode()[:200]}")

    start = json.loads(body)
    job_id = start.get("background_job_id")
    if not job_id:
        raise RuntimeError(f"no background_job_id — keys: {list(start.keys())}")

    data = _pl_poll(job_id)
    items = (data.get("images") or data.get("frames") or
             (data.get("data") if isinstance(data.get("data"), list) else None))
    if not items:
        raise RuntimeError(f"unexpected API shape: {list(data.keys())}")

    frames = []
    for item in items:
        raw = item if isinstance(item, str) else (
            item.get("base64") or item.get("image") or item.get("b64_json") or "")
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        f = _PILImage.open(io.BytesIO(base64.b64decode(raw))).convert("RGBA")
        if f.size != (_FS_FRAME, _FS_FRAME):
            f = f.resize((_FS_FRAME, _FS_FRAME), _PILImage.NEAREST)
        frames.append(f)

    ref_pal = img.convert("RGB").quantize(colors=256, method=_PILImage.Quantize.MEDIANCUT)
    locked = []
    for f in frames:
        r, g, b, a = f.split()
        snapped = _PILImage.merge("RGB", (r, g, b)) \
                            .quantize(palette=ref_pal, dither=_PILImage.Dither.NONE) \
                            .convert("RGB").convert("RGBA")
        snapped.putalpha(a)
        locked.append(snapped)

    n = len(locked)
    sheet = _PILImage.new("RGBA", (n * _FS_FRAME, _FS_FRAME), (0, 0, 0, 0))
    for i, f in enumerate(locked):
        sheet.paste(f, (i * _FS_FRAME, 0))
    out = io.BytesIO(); sheet.save(out, format="PNG")
    return out.getvalue(), n


@dataclass
class _SpriteJob:
    id: str
    total: int = _FS_GENS
    done: int = 0
    status: str = "pending"
    error: str | None = None
    completed: list = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


_SJOBS: dict[str, _SpriteJob] = {}
_SJOBS_LOCK = threading.Lock()


@app.get("/api/sprite/list")
async def sprite_list(request: Request) -> JSONResponse:
    _require_auth(request)
    sprites = []
    for p in sorted(SPRITES_DIR.glob("*/meta.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            sprites.append(json.loads(p.read_text()))
        except Exception:
            pass
    return JSONResponse({"sprites": sprites})


@app.get("/api/sprite/jobs/{job_id}")
async def sprite_job_status(job_id: str, request: Request) -> JSONResponse:
    _require_auth(request)
    with _SJOBS_LOCK:
        sj = _SJOBS.get(job_id)
    if not sj:
        raise HTTPException(404, "job not found")
    return JSONResponse({"status": sj.status, "done": sj.done,
                         "total": sj.total, "completed": sj.completed,
                         "error": sj.error})


@app.post("/api/sprite/generate")
async def sprite_generate(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    _require_auth(request)
    if not PIXELLAB_SECRET:
        raise HTTPException(400, "PIXELLAB_SECRET not configured on server")
    ref_bytes = await file.read()
    fname = file.filename or "fish.png"

    sj = _SpriteJob(id=uuid.uuid4().hex)
    with _SJOBS_LOCK:
        _SJOBS[sj.id] = sj

    def worker() -> None:
        sj.status = "running"
        for _ in range(_FS_GENS):
            try:
                sheet_bytes, n_frames = _generate_one(ref_bytes)
                sid = uuid.uuid4().hex
                d = SPRITES_DIR / sid
                d.mkdir(parents=True, exist_ok=True)
                (d / "sheet.png").write_bytes(sheet_bytes)
                meta = {"id": sid, "name": fname, "cols": n_frames,
                        "rows": 1, "frameW": _FS_FRAME, "frameH": _FS_FRAME,
                        "created_at": time.time()}
                (d / "meta.json").write_text(json.dumps(meta))
                sj.completed.append(meta)
            except Exception as e:
                sj.error = str(e)
            finally:
                sj.done += 1
        sj.status = "done"

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse({"job_id": sj.id})


@app.get("/api/sprite/{sprite_id}/image")
async def sprite_image(sprite_id: str, request: Request) -> Response:
    _require_auth(request)
    p = SPRITES_DIR / sprite_id / "sheet.png"
    if not p.exists():
        raise HTTPException(404, "sprite not found")
    return Response(content=p.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.delete("/api/sprite/{sprite_id}")
async def sprite_delete(sprite_id: str, request: Request) -> JSONResponse:
    _require_auth(request)
    d = SPRITES_DIR / sprite_id
    if not d.exists():
        raise HTTPException(404, "sprite not found")
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    return JSONResponse({"ok": True})



# ============================================================
# Fish Studio -- sprite generation via PixelLab
# ============================================================

try:
    from PIL import Image as _PILImage
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

PIXELLAB_SECRET = os.getenv("PIXELLAB_SECRET", "")
SPRITES_DIR = APP_DIR / "sprites"
SPRITES_DIR.mkdir(exist_ok=True)

_FS_FRAME  = 256
_FS_FRAMES = 8
_FS_GENS   = 3
_FS_ACTION = (
    "the fish swims forward, side view, tail and fins gently undulating, "
    "eyes not moving. no color change. the body color stays exactly the same. "
    "no blink. no shadow."
)


def _pl_headers() -> dict:
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {PIXELLAB_SECRET}"}


def _pl_poll(job_id: str) -> dict:
    deadline = time.time() + 360
    while time.time() < deadline:
        conn = http.client.HTTPSConnection("api.pixellab.ai")
        conn.request("GET", f"/v2/background-jobs/{job_id}", headers=_pl_headers())
        res = conn.getresponse(); body = res.read(); conn.close()
        if res.status != 200:
            raise RuntimeError(f"poll {res.status}: {body.decode()[:200]}")
        pl = json.loads(body)
        st = pl.get("status")
        if st == "completed":
            return pl.get("last_response") or pl
        if st in ("failed", "error", "cancelled"):
            raise RuntimeError(f"PixelLab job {job_id} {st}")
        time.sleep(3)
    raise RuntimeError("PixelLab job timed out")


def _generate_one(ref_png: bytes) -> tuple[bytes, int]:
    if not _PIL_OK:
        raise RuntimeError("Pillow not installed -- pip install Pillow")

    img = _PILImage.open(io.BytesIO(ref_png)).convert("RGBA")
    if img.size != (_FS_FRAME, _FS_FRAME):
        img = img.resize((_FS_FRAME, _FS_FRAME), _PILImage.NEAREST)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    ref_b64 = {"type": "base64",
                "base64": f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}",
                "format": "png"}

    payload = json.dumps({
        "first_frame":   ref_b64,
        "last_frame":    ref_b64,
        "action":        _FS_ACTION,
        "frame_count":   _FS_FRAMES,
        "no_background": True,
        "seed":          random.randint(1, 2 ** 31 - 1),
    })
    conn = http.client.HTTPSConnection("api.pixellab.ai")
    conn.request("POST", "/v2/animate-with-text-v3", payload, _pl_headers())
    res = conn.getresponse(); body = res.read(); conn.close()
    if res.status not in (200, 202):
        raise RuntimeError(f"PixelLab {res.status}: {body.decode()[:200]}")

    start = json.loads(body)
    job_id = start.get("background_job_id")
    if not job_id:
        raise RuntimeError(f"no background_job_id -- keys: {list(start.keys())}")

    data = _pl_poll(job_id)
    items = (data.get("images") or data.get("frames") or
             (data.get("data") if isinstance(data.get("data"), list) else None))
    if not items:
        raise RuntimeError(f"unexpected API shape: {list(data.keys())}")

    frames = []
    for item in items:
        raw = item if isinstance(item, str) else (
            item.get("base64") or item.get("image") or item.get("b64_json") or "")
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        f = _PILImage.open(io.BytesIO(base64.b64decode(raw))).convert("RGBA")
        if f.size != (_FS_FRAME, _FS_FRAME):
            f = f.resize((_FS_FRAME, _FS_FRAME), _PILImage.NEAREST)
        frames.append(f)

    ref_pal = img.convert("RGB").quantize(colors=256, method=_PILImage.Quantize.MEDIANCUT)
    locked = []
    for f in frames:
        r, g, b, a = f.split()
        snapped = _PILImage.merge("RGB", (r, g, b))                             .quantize(palette=ref_pal, dither=_PILImage.Dither.NONE)                             .convert("RGB").convert("RGBA")
        snapped.putalpha(a)
        locked.append(snapped)

    n = len(locked)
    sheet = _PILImage.new("RGBA", (n * _FS_FRAME, _FS_FRAME), (0, 0, 0, 0))
    for i, f in enumerate(locked):
        sheet.paste(f, (i * _FS_FRAME, 0))
    out = io.BytesIO(); sheet.save(out, format="PNG")
    return out.getvalue(), n


@dataclass
class _SpriteJob:
    id: str
    total: int = _FS_GENS
    done: int = 0
    status: str = "pending"
    error: str | None = None
    completed: list = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


_SJOBS: dict[str, _SpriteJob] = {}
_SJOBS_LOCK = threading.Lock()


@app.get("/api/sprite/list")
async def sprite_list(request: Request) -> JSONResponse:
    _require_auth(request)
    sprites = []
    for p in sorted(SPRITES_DIR.glob("*/meta.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            sprites.append(json.loads(p.read_text()))
        except Exception:
            pass
    return JSONResponse({"sprites": sprites})


@app.get("/api/sprite/jobs/{job_id}")
async def sprite_job_status(job_id: str, request: Request) -> JSONResponse:
    _require_auth(request)
    with _SJOBS_LOCK:
        sj = _SJOBS.get(job_id)
    if not sj:
        raise HTTPException(404, "job not found")
    return JSONResponse({"status": sj.status, "done": sj.done,
                         "total": sj.total, "completed": sj.completed,
                         "error": sj.error})


@app.post("/api/sprite/generate")
async def sprite_generate(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    _require_auth(request)
    if not PIXELLAB_SECRET:
        raise HTTPException(400, "PIXELLAB_SECRET not configured on server")
    ref_bytes = await file.read()
    fname = file.filename or "fish.png"

    sj = _SpriteJob(id=uuid.uuid4().hex)
    with _SJOBS_LOCK:
        _SJOBS[sj.id] = sj

    def worker() -> None:
        sj.status = "running"
        for _ in range(_FS_GENS):
            try:
                sheet_bytes, n_frames = _generate_one(ref_bytes)
                sid = uuid.uuid4().hex
                d = SPRITES_DIR / sid
                d.mkdir(parents=True, exist_ok=True)
                (d / "sheet.png").write_bytes(sheet_bytes)
                meta = {"id": sid, "name": fname, "cols": n_frames,
                        "rows": 1, "frameW": _FS_FRAME, "frameH": _FS_FRAME,
                        "created_at": time.time()}
                (d / "meta.json").write_text(json.dumps(meta))
                sj.completed.append(meta)
            except Exception as e:
                sj.error = str(e)
            finally:
                sj.done += 1
        sj.status = "done"

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse({"job_id": sj.id})


@app.get("/api/sprite/{sprite_id}/image")
async def sprite_image(sprite_id: str, request: Request) -> Response:
    _require_auth(request)
    p = SPRITES_DIR / sprite_id / "sheet.png"
    if not p.exists():
        raise HTTPException(404, "sprite not found")
    return Response(content=p.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.delete("/api/sprite/{sprite_id}")
async def sprite_delete(sprite_id: str, request: Request) -> JSONResponse:
    _require_auth(request)
    d = SPRITES_DIR / sprite_id
    if not d.exists():
        raise HTTPException(404, "sprite not found")
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    return JSONResponse({"ok": True})
