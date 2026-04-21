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

import io
import os
import re
import secrets
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer

from excel_parser import parse_instructions, parse_questions
from tts import FEMALE_VOICES, MALE_VOICES, MODEL_DEFAULT, TTSError, build_segment

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
