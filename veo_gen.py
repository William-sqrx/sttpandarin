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

VEO_MODEL = "veo-3.1-generate-001"
VEO_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "project-5e5ddf83-301a-45ce-839")
VEO_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

VEO_VIDEOS_PER_CALL = 4      # one Veo call yields this many clip variants
VEO_DURATION_SECS = 4
VEO_POLL_SECS = 10
VEO_TIMEOUT_SECS = 600       # give up on a single fish after 10 minutes

# ----- Sprite-sheet post-processing -----------------------------------------
SHEET_COLS = 5
SHEET_ROWS = 5
SHEET_FRAMES = 24            # 5x5 grid with the bottom-right cell left empty
CROP_BLACK_THRESHOLD = 24    # max(R,G,B) <= this counts as black background
CROP_PADDING = 8             # safety margin so dark fish outlines aren't clipped
MAX_CELL_PX = 256            # downscale cropped frames so cells stay reasonable

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
    if not VEO_PROJECT:
        return False, "GOOGLE_CLOUD_PROJECT not set"
    cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not cred:
        return False, "GOOGLE_APPLICATION_CREDENTIALS not set"
    if not os.path.isfile(cred):
        return False, f"service-account file not found: {cred}"
    return True, ""


def generate_videos(ref_png: bytes) -> list[bytes]:
    """Animate one fish reference image into VEO_VIDEOS_PER_CALL looping MP4
    clips via Veo 3.1 on Vertex AI. The reference is used as the first AND
    last frame so each clip is a seamless loop. Blocks until Veo finishes
    (typically a few minutes). Returns the raw MP4 bytes for every clip.

    Raises RuntimeError on any failure — the caller handles retry/counters.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True, project=VEO_PROJECT, location=VEO_LOCATION,
    )
    ref_image = types.Image(image_bytes=ref_png, mime_type="image/png")

    source = types.GenerateVideosSource(prompt=VEO_PROMPT, image=ref_image)
    config = types.GenerateVideosConfig(
        aspect_ratio="16:9",
        number_of_videos=VEO_VIDEOS_PER_CALL,
        duration_seconds=VEO_DURATION_SECS,
        person_generation="allow_all",
        generate_audio=False,
        resolution="720p",
        seed=0,
        last_frame=ref_image,
    )

    operation = client.models.generate_videos(
        model=VEO_MODEL, source=source, config=config,
    )

    deadline = time.time() + VEO_TIMEOUT_SECS
    while not operation.done:
        if time.time() > deadline:
            raise RuntimeError(f"Veo timed out after {VEO_TIMEOUT_SECS}s")
        time.sleep(VEO_POLL_SECS)
        operation = client.operations.get(operation)

    response = operation.result
    if not response:
        raise RuntimeError("Veo returned no result")
    generated = response.generated_videos or []
    if not generated:
        raise RuntimeError("Veo produced no videos")

    out: list[bytes] = []
    for gv in generated:
        vid = getattr(gv, "video", None)
        if vid is None:
            continue
        data = getattr(vid, "video_bytes", None)
        if not data and getattr(vid, "uri", None):
            # SDK handed back a GCS/Files URI instead of inline bytes —
            # pull the bytes down explicitly.
            try:
                client.files.download(file=vid)
                data = getattr(vid, "video_bytes", None)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"Veo returned a URI ({vid.uri}) and download failed: {e}")
        if data:
            out.append(bytes(data))
    if not out:
        raise RuntimeError("Veo videos had no bytes")
    return out


def video_to_sprite_sheet(mp4_bytes: bytes):
    """Turn one MP4 clip into a SHEET_COLS x SHEET_ROWS sprite sheet.

    Every frame is cropped to the fish's bounding box — the UNION of the
    non-black region across ALL frames, so the body is never clipped in any
    frame. SHEET_FRAMES frames are sampled evenly and packed row-major into
    the grid; the final (bottom-right) cell is left empty.

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

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(mp4_bytes)
        tmp = tf.name
    try:
        # Pass 1 — count frames and OR together a non-black mask.
        union = None
        height = width = 0
        total = 0
        reader = imageio.get_reader(tmp, format="ffmpeg")
        try:
            for frame in reader:
                arr = np.asarray(frame)[:, :, :3]
                if union is None:
                    height, width = arr.shape[0], arr.shape[1]
                    union = np.zeros((height, width), dtype=bool)
                union |= arr.max(axis=2) > CROP_BLACK_THRESHOLD
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

        # Even sample across the clip. i*total/N (rather than i*(total-1)/(N-1))
        # skips the duplicated final loop frame for a clean SHEET_FRAMES loop.
        want: dict[int, list[int]] = {}
        for pos in range(SHEET_FRAMES):
            fidx = min(total - 1, round(pos * total / SHEET_FRAMES))
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

    if any(s is None for s in sampled):
        raise RuntimeError("failed to sample every frame from the clip")

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
    return buf.getvalue(), SHEET_COLS, SHEET_ROWS, SHEET_FRAMES, cell_w, cell_h
