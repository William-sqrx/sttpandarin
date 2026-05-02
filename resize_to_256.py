#!/usr/bin/env python3
"""Resize every image in a folder to 256×256 and save to an output folder.

Usage:
  python3 resize_to_256.py <input_folder> <output_folder>

Requires: Pillow  (pip install Pillow)
"""
import sys
from pathlib import Path
from PIL import Image

EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def main() -> int:
    if len(sys.argv) < 3:
        sys.exit("Usage: resize_to_256.py <input_folder> <output_folder>")
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    if not src.is_dir():
        sys.exit(f"Not a directory: {src}")
    dst.mkdir(parents=True, exist_ok=True)
    imgs = sorted(p for p in src.iterdir() if p.suffix.lower() in EXTS)
    if not imgs:
        sys.exit(f"No images found in {src}")
    for p in imgs:
        img = Image.open(p).convert("RGBA")
        img = img.resize((256, 256), Image.NEAREST)
        out = dst / (p.stem + ".png")
        img.save(out, "PNG")
        print(f"  {p.name} → {out.name}")
    print(f"Done — {len(imgs)} image(s) resized to 256×256 → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
