#!/usr/bin/env python3
"""Run-once batch animator: for every PNG in the input folder, call
PixelLab 5 times to produce 5 swim-in-place sprite sheets per fish.

Each completed sheet is written to disk locally AND optionally POSTed
to a remote webapp (the prod deploy at https://...onrender.com) so
others can watch the gallery fill in real time. The remote upload is
opt-in via BATCH_UPLOAD_URL + BATCH_UPLOAD_KEY env vars.

Resumable: existing output files are skipped, so re-running picks up
where it left off if the previous run was interrupted.

Output layout (served by the gallery at /fishanims):
  webapp/fish_anims/<stem>/<n>.png       (sprite sheet)
  webapp/fish_anims/<stem>/<n>.json      (frame meta)

Usage:
  PIXELLAB_SECRET=xxx \
  BATCH_UPLOAD_URL=https://your-app.onrender.com \
  BATCH_UPLOAD_KEY=xxx \
  python3 batch_animate_fish.py [input_folder] [--per-fish 5]

Default input folder: webapp/Chinesely Fish (256)/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = APP_DIR / "Chinesely Fish (256)"
OUT_DIR = APP_DIR / "fish_anims"

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(APP_DIR / ".env")
except ImportError:
    pass


def _upload(stem: str, idx: int, sheet_bytes: bytes, meta: dict,
            url: str, key: str) -> tuple[bool, str]:
    """POST a finished sheet to the remote gallery. Returns (ok, msg)."""
    import requests  # transitive via app import; available in venv

    try:
        r = requests.post(
            f"{url.rstrip('/')}/api/fishanims/{stem}/{idx}/upload",
            headers={"x-batch-key": key},
            files={"sheet": (f"{idx}.png", sheet_bytes, "image/png")},
            data={"meta": json.dumps(meta)},
            timeout=60,
        )
    except requests.RequestException as e:
        return False, f"upload error: {e}"
    if r.status_code != 200:
        return False, f"upload {r.status_code}: {r.text[:120]}"
    return True, "uploaded"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", default=str(DEFAULT_INPUT),
                    help="folder of 256x256 PNG references")
    ap.add_argument("--per-fish", type=int, default=5,
                    help="number of sprite sheets to generate per fish")
    args = ap.parse_args()

    if not os.getenv("PIXELLAB_SECRET"):
        sys.exit("PIXELLAB_SECRET env var not set")

    src = Path(args.input)
    if not src.is_dir():
        sys.exit(f"input folder not found: {src}")

    pngs = sorted(p for p in src.glob("*.png") if any(c.isalpha() for c in p.stem))
    if not pngs:
        sys.exit(f"no PNGs in {src}")

    OUT_DIR.mkdir(exist_ok=True)

    sys.path.insert(0, str(APP_DIR))
    from app import _generate_one  # noqa: E402

    upload_url = os.getenv("BATCH_UPLOAD_URL", "").strip()
    upload_key = os.getenv("BATCH_UPLOAD_KEY", "").strip()
    upload_enabled = bool(upload_url and upload_key)

    n_total = len(pngs) * args.per_fish
    n_done = n_skipped = n_failed = 0
    n_uploaded = n_upload_failed = 0
    t_start = time.time()

    print(f"input:   {src}")
    print(f"output:  {OUT_DIR}")
    print(f"upload:  {'→ ' + upload_url if upload_enabled else 'disabled (set BATCH_UPLOAD_URL + BATCH_UPLOAD_KEY)'}")
    print(f"fish:    {len(pngs)}  x  {args.per_fish} sheets each = {n_total} jobs\n")

    for png in pngs:
        stem = png.stem
        species_dir = OUT_DIR / stem
        species_dir.mkdir(exist_ok=True)
        ref_bytes = png.read_bytes()

        for idx in range(1, args.per_fish + 1):
            sheet_path = species_dir / f"{idx}.png"
            meta_path = species_dir / f"{idx}.json"
            if sheet_path.exists() and meta_path.exists():
                n_skipped += 1
                continue

            tag = f"[{stem} {idx}/{args.per_fish}]"
            print(f"{tag} generating...", flush=True)
            t0 = time.time()
            try:
                sheet_bytes, frames, frame_w = _generate_one(ref_bytes)
            except Exception as e:  # noqa: BLE001
                n_failed += 1
                print(f"{tag} FAILED: {e}", flush=True)
                continue

            meta = {
                "frames": frames,
                "frameW": frame_w,
                "frameH": frame_w,
                "created_at": time.time(),
            }
            sheet_path.write_bytes(sheet_bytes)
            meta_path.write_text(json.dumps(meta))
            n_done += 1
            elapsed = time.time() - t0

            up_msg = ""
            if upload_enabled:
                ok, msg = _upload(stem, idx, sheet_bytes, meta,
                                  upload_url, upload_key)
                if ok:
                    n_uploaded += 1
                    up_msg = " ↑prod"
                else:
                    n_upload_failed += 1
                    up_msg = f" ⚠ {msg}"

            avg = (time.time() - t_start) / max(n_done, 1)
            remaining = n_total - n_done - n_skipped - n_failed
            eta = remaining * avg
            print(f"{tag} ok ({frames}f, {elapsed:.1f}s){up_msg}  "
                  f"done {n_done}  skip {n_skipped}  fail {n_failed}  "
                  f"eta ~{eta/60:.1f}m", flush=True)

    print(f"\nfinished. generated {n_done}, skipped {n_skipped}, failed {n_failed}")
    if upload_enabled:
        print(f"uploaded:  {n_uploaded}, upload-failed: {n_upload_failed}")
    print(f"total time: {(time.time() - t_start)/60:.1f}m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
