"""Veo 3.1 video generation for the fish-animations gallery.

Replaces the old PixelLab sprite-sheet pipeline. Each fish reference image
is animated into short looping MP4 clips: the reference is passed as BOTH
the first frame and the last frame so every clip loops seamlessly.

Runs on Vertex AI, so the server needs Google Cloud credentials — a
service-account JSON pointed at by GOOGLE_APPLICATION_CREDENTIALS. The
target project/location come from env vars (defaults below).
"""
from __future__ import annotations

import os
import time

import gemini_client

VEO_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "hskfish")
VEO_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

# The Veo model id differs between the two auth paths: Vertex AI publishes
# "veo-3.1-generate-001"; the Developer (AI Studio) API uses a "-preview"
# suffix. Pick a sane default per mode and let VEO_MODEL override it from the
# Render dashboard if Google renames the model.
_DEFAULT_VEO_MODEL = ("veo-3.1-generate-preview"
                      if gemini_client.using_api_key()
                      else "veo-3.1-generate-001")
VEO_MODEL = os.getenv("VEO_MODEL", _DEFAULT_VEO_MODEL)

# personGeneration="allow_all" is accepted on BOTH Vertex and the Developer
# API (verified by direct probe). Counter-intuitively the Developer API
# *rejects* "allow_adult"/"dont_allow" for video (400 not-supported) — so do
# NOT branch this per mode; allow_all is the value that works everywhere.
_PERSON_GENERATION = "allow_all"

VEO_VIDEOS_PER_CALL = 4      # one Veo call yields this many clip variants
VEO_DURATION_SECS = 4
VEO_POLL_SECS = 10
VEO_TIMEOUT_SECS = 600       # give up on a single fish after 10 minutes

# The Developer (API-key) path has much tighter Veo rate limits than Vertex.
# Firing all VEO_VIDEOS_PER_CALL requests at once trips 429 RESOURCE_EXHAUSTED,
# so run them one at a time there (each clip submits + polls sequentially, well
# under any per-minute cap). Vertex can absorb the full concurrent burst.
# Override with VEO_CONCURRENCY from the dashboard if quota allows more.
_DEFAULT_CONCURRENCY = 1 if gemini_client.using_api_key() else VEO_VIDEOS_PER_CALL
VEO_CONCURRENCY = max(1, int(os.getenv("VEO_CONCURRENCY", _DEFAULT_CONCURRENCY)))

# ----- Sprite-sheet post-processing -----------------------------------------
SHEET_COLS = 5
SHEET_ROWS = 5
SHEET_FRAMES = 24            # 5x5 grid with the bottom-right cell left empty
CROP_BLACK_THRESHOLD = 24    # max(R,G,B) <= this counts as black background
CROP_PADDING = 8             # safety margin so dark fish outlines aren't clipped
MAX_CELL_PX = 256            # downscale cropped frames so cells stay reasonable
LOOP_SEARCH_FRAC = 0.6       # only look for the loop-end frame past this point

VEO_PROMPT = """Animate this fish with very subtle smooth swimming motion only.

CRITICAL RULE:
Keep the fish design completely unchanged frame to frame.
The eye must remain exactly identical to the input image at all times:
- same size
- same shape
- same position
- same black pupil
- same white highlight
- same outline
- same cheek blush
Do not redesign, restyle, reshape, enlarge, shrink, rotate, or animate the eye. The eye should look locked in place.

Loop rule:
The first frame and the last frame must be visually identical so the video forms a perfect seamless loop.

Background rule:
The background is solid pure black (#000000).
Keep the background solid pure black at all times.
Do not add water, bubbles, light rays, scenery, or any background detail.

Motion rules:
- fish swims forward smoothly
- very small tail swaying
- very subtle fin movement
- very slight body bobbing
- optional tiny upward drift
- movement should be calm and polished

Preserve exactly:
- same body shape
- same orange color
- same fins
- same mouth
- same proportions
- same cute 2D game-art style

Do not:
- change facial features
- change expression
- morph the body
- distort the head
- add new objects
- change camera
- change background

The result should feel like the original fish image was gently animated, not redrawn."""


def veo_configured() -> tuple[bool, str]:
    """Return (ok, reason). When ok is False, generation will fail — the
    batch/start endpoint surfaces `reason` so the user gets a clear error
    instead of a mid-batch crash."""
    # Developer API key path — nothing else required.
    if gemini_client.using_api_key():
        return True, ""
    # Vertex AI path — needs a project + service-account JSON.
    if not VEO_PROJECT:
        return False, "set GEMINI_API_KEY (recommended) or GOOGLE_CLOUD_PROJECT"
    cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not cred:
        return False, ("set GEMINI_API_KEY (recommended) or "
                       "GOOGLE_APPLICATION_CREDENTIALS")
    if not os.path.isfile(cred):
        return False, f"service-account file not found: {cred}"
    return True, ""


def _run_one_clip(client, types_mod, ref_image, prompt_text: str,
                  seed: int, should_stop=None) -> bytes:
    """Run a single Veo generation (number_of_videos=1) and return the MP4
    bytes. Polls until done, aborting promptly if should_stop fires."""
    source = types_mod.GenerateVideosSource(prompt=prompt_text, image=ref_image)
    config_kwargs = dict(
        aspect_ratio="16:9",
        number_of_videos=1,
        duration_seconds=VEO_DURATION_SECS,
        person_generation=_PERSON_GENERATION,
        resolution="720p",
        last_frame=ref_image,
    )
    # `seed` and `generate_audio` are Vertex-only ("Enterprise Agent Platform"
    # mode); the Developer (API-key) path rejects both. Gate them on the Vertex
    # path only. On the API-key path Veo 3 generates audio by default — that's
    # harmless here since we sample video frames into the sprite sheet and
    # throw the soundtrack away. Seed is dropped too (the 4 concurrent calls
    # still vary via Veo's own internal randomness).
    if not gemini_client.using_api_key():
        config_kwargs["seed"] = seed
        config_kwargs["generate_audio"] = False
    config = types_mod.GenerateVideosConfig(**config_kwargs)
    operation = client.models.generate_videos(
        model=VEO_MODEL, source=source, config=config,
    )
    deadline = time.time() + VEO_TIMEOUT_SECS
    while not operation.done:
        if time.time() > deadline:
            raise RuntimeError(f"Veo timed out after {VEO_TIMEOUT_SECS}s")
        # Sleep in 1s slices so a Stop press is noticed promptly.
        for _ in range(VEO_POLL_SECS):
            if should_stop and should_stop():
                raise RuntimeError("Veo wait aborted — stop requested")
            time.sleep(1)
        operation = client.operations.get(operation)

    response = operation.result
    if not response or not response.generated_videos:
        raise RuntimeError("Veo produced no video")
    gv = response.generated_videos[0]
    vid = getattr(gv, "video", None)
    data = getattr(vid, "video_bytes", None) if vid is not None else None
    if not data and vid is not None and getattr(vid, "uri", None):
        # SDK handed back a GCS/Files URI instead of inline bytes — pull
        # the bytes down explicitly.
        try:
            client.files.download(file=vid)
            data = getattr(vid, "video_bytes", None)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Veo returned a URI ({vid.uri}) and download failed: {e}")
    if not data:
        raise RuntimeError("Veo video had no bytes")
    return bytes(data)


def generate_videos(ref_png: bytes, should_stop=None,
                    prompt: str | None = None, on_progress=None) -> list[bytes]:
    """Animate one fish reference image into VEO_VIDEOS_PER_CALL looping MP4
    clips via Veo 3.1 on Vertex AI. The reference is used as the first AND
    last frame so each clip is a seamless loop.

    NOTE: with both a first frame and a last_frame pinned, Veo runs in
    interpolation mode and returns only ONE video per call regardless of
    number_of_videos. So this fires VEO_VIDEOS_PER_CALL separate calls
    (each with its own random seed for variety) concurrently — roughly the
    same wall-clock time as one call, but VEO_VIDEOS_PER_CALL distinct clips.

    `prompt`, if given (and non-blank), is the text sent to Veo; otherwise
    the built-in VEO_PROMPT is used. `should_stop`, if given, is a no-arg
    callable polled while waiting — a Stop press aborts within ~1s.
    `on_progress(elapsed_secs, done, total)`, if given, is called every
    poll so the caller can surface live progress.

    Returns the MP4 bytes of every clip that succeeded (at least one);
    raises RuntimeError only if they all fail.
    """
    import concurrent.futures
    import random

    from google.genai import types

    # API-key (Developer API) when GEMINI_API_KEY is set, else Vertex AI.
    client = gemini_client.new_client(VEO_PROJECT, VEO_LOCATION)
    ref_image = types.Image(image_bytes=ref_png, mime_type="image/png")
    prompt_text = (prompt or "").strip() or VEO_PROMPT

    seeds = [random.randint(1, 2 ** 31 - 1)
             for _ in range(VEO_VIDEOS_PER_CALL)]
    out: list[bytes] = []
    errors: list[Exception] = []
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=VEO_CONCURRENCY) as pool:
        futures = [
            pool.submit(_run_one_clip, client, types, ref_image,
                        prompt_text, seed, should_stop)
            for seed in seeds
        ]
        while True:
            done = sum(1 for f in futures if f.done())
            if on_progress:
                try:
                    on_progress(int(time.time() - start), done, len(futures))
                except Exception:  # noqa: BLE001
                    pass
            if done == len(futures) or (should_stop and should_stop()):
                break
            time.sleep(VEO_POLL_SECS)
        for f in futures:
            try:
                out.append(f.result())
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    if not out:
        raise RuntimeError(
            f"all {VEO_VIDEOS_PER_CALL} Veo calls failed — "
            f"{errors[0] if errors else 'unknown error'}")
    return out


def video_to_sprite_sheet(mp4_bytes: bytes):
    """Turn one MP4 clip into a SHEET_COLS x SHEET_ROWS sprite sheet.

    Every frame is cropped to the fish's bounding box — the UNION of the
    non-black region across ALL frames, so the body is never clipped.

    For a stutter-free loop the clip is cut at its natural loop point: the
    frame in the back portion of the clip that looks most like frame 0 is
    found, and only [0 .. loop_end] is sampled. SHEET_FRAMES-1 unique
    frames are taken evenly across that span, and the final cell is set
    equal to the first — so the sprite sheet's last frame == first frame
    and playback wraps cleanly.

    Decoding is two-pass and streamed (one frame in memory at a time) to
    stay well under the dyno's RAM ceiling.

    Returns (png_bytes, cols, rows, frames, cell_w, cell_h).
    """
    import io
    import os
    import tempfile

    import imageio
    import numpy as np
    from PIL import Image

    # SHEET_FRAMES cells total; the last one repeats the first, so only
    # this many unique frames are sampled from the clip.
    unique = SHEET_FRAMES - 1

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(mp4_bytes)
        tmp = tf.name
    try:
        # Pass 1 — non-black mask + frame count + per-frame similarity to
        # frame 0 (a small grayscale thumbnail diff) for loop detection.
        union = None
        height = width = 0
        total = 0
        ref_small = None
        diffs: list[float] = []
        reader = imageio.get_reader(tmp, format="ffmpeg")
        try:
            for frame in reader:
                arr = np.asarray(frame)[:, :, :3]
                if union is None:
                    height, width = arr.shape[0], arr.shape[1]
                    union = np.zeros((height, width), dtype=bool)
                union |= arr.max(axis=2) > CROP_BLACK_THRESHOLD
                small = np.asarray(
                    Image.fromarray(arr).convert("L").resize((48, 48)),
                ).astype(np.int16)
                if ref_small is None:
                    ref_small = small
                diffs.append(float(np.abs(small - ref_small).mean()))
                total += 1
        finally:
            reader.close()

        if total == 0 or union is None:
            raise RuntimeError("clip had no frames")
        ys, xs = np.where(union)
        if len(xs) == 0:
            raise RuntimeError("clip frames were entirely black — no fish found")

        x0 = max(0, int(xs.min()) - CROP_PADDING)
        y0 = max(0, int(ys.min()) - CROP_PADDING)
        x1 = min(width, int(xs.max()) + 1 + CROP_PADDING)
        y1 = min(height, int(ys.max()) + 1 + CROP_PADDING)

        # Loop-point detection — the frame in the back LOOP_SEARCH_FRAC of
        # the clip closest to frame 0. Cutting the clip there means the
        # fish has genuinely returned to its start pose, so wrapping the
        # sprite sheet doesn't jump.
        search_start = max(1, int(total * LOOP_SEARCH_FRAC))
        if search_start >= total:
            loop_end = total - 1
        else:
            loop_end = search_start + int(np.argmin(diffs[search_start:]))
        loop_end = max(unique, loop_end)  # need at least `unique` frames

        # Sample `unique` frames evenly across [0, loop_end]; the last cell
        # is filled with a copy of frame 0 afterwards.
        want: dict[int, list[int]] = {}
        for pos in range(unique):
            fidx = min(loop_end, round(pos * loop_end / unique))
            want.setdefault(fidx, []).append(pos)

        # Pass 2 — keep only the sampled frames, already cropped.
        sampled: list = [None] * SHEET_FRAMES
        reader = imageio.get_reader(tmp, format="ffmpeg")
        try:
            for fi, frame in enumerate(reader):
                if fi in want:
                    crop = np.asarray(frame)[y0:y1, x0:x1, :3].copy()
                    for pos in want[fi]:
                        sampled[pos] = crop
        finally:
            reader.close()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if any(sampled[p] is None for p in range(unique)):
        raise RuntimeError("failed to sample every frame from the clip")

    # The final cell repeats frame 0 — so the sheet's last frame == its
    # first frame — but it is NOT one of the played frames: playback runs
    # the `unique` frames 0..unique-1 and wraps straight back to 0. Playing
    # the duplicate would show frame 0 twice in a row (a visible pause); the
    # repeated cell is kept only as the conventional closing frame.
    sampled[SHEET_FRAMES - 1] = sampled[0]

    crop_h, crop_w = y1 - y0, x1 - x0
    scale = min(1.0, MAX_CELL_PX / max(crop_w, crop_h))
    cell_w = max(1, round(crop_w * scale))
    cell_h = max(1, round(crop_h * scale))

    sheet = Image.new(
        "RGB", (SHEET_COLS * cell_w, SHEET_ROWS * cell_h), (0, 0, 0))
    for pos, crop in enumerate(sampled):
        img = Image.fromarray(crop)
        if scale < 1.0:
            img = img.resize((cell_w, cell_h), Image.LANCZOS)
        col = pos % SHEET_COLS
        row = pos // SHEET_COLS
        sheet.paste(img, (col * cell_w, row * cell_h))

    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    # `frames` is the PLAYED count (unique) — excludes the duplicate closing
    # cell so playback wraps unique-1 -> 0 with no doubled frame / pause.
    return buf.getvalue(), SHEET_COLS, SHEET_ROWS, unique, cell_w, cell_h
