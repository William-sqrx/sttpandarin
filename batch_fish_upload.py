#!/usr/bin/env python3
"""One-shot uploader: send every PNG in a folder to the always-running
chinesely-backend's /api/sprites/batch endpoint, then exit. The server's
single worker generates 3 sprite sheets per file sequentially, so this
laptop can be closed once the script returns.

Usage:
  SPRITE_ADMIN_KEY=xxx python3 batch_fish_upload.py [folder] [--url URL]

Defaults:
  folder = ../chinesely_app/chinesely-frontend/src/assets/images/test_fish
  url    = https://chinesely-backend-1.onrender.com

Pure stdlib — no third-party deps.
"""
import argparse
import getpass
import json
import os
import ssl
import sys
import time
import uuid
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_URL = "https://chinesely-backend-1.onrender.com"
DEFAULT_DIR = (Path(__file__).resolve().parent.parent
               / "chinesely_app" / "chinesely-frontend"
               / "src" / "assets" / "images" / "test_fish")


def _conn(url: str):
    u = urlparse(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)
    if u.scheme == "https":
        return HTTPSConnection(host, port, timeout=600,
                               context=ssl.create_default_context())
    return HTTPConnection(host, port, timeout=600)


def _multipart(files):
    """Build a multipart/form-data body. files = [(field, filename, bytes)]."""
    boundary = "----chinesely" + uuid.uuid4().hex
    parts = []
    for field, filename, data in files:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: image/png\r\n\r\n".encode()
        )
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=str(DEFAULT_DIR),
                    help="folder of PNG fish references")
    ap.add_argument("--url", default=DEFAULT_URL, help="backend base URL")
    ap.add_argument("--key", default=os.getenv("SPRITE_ADMIN_KEY"),
                    help="admin key (or set SPRITE_ADMIN_KEY env var)")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")
    pngs = sorted(folder.glob("*.png"))
    if not pngs:
        sys.exit(f"no .png files in {folder}")
    key = args.key or getpass.getpass("SPRITE_ADMIN_KEY: ")

    base = args.url.rstrip("/")
    url_parts = urlparse(base)

    print(f"submitting {len(pngs)} file(s) → {base}/api/sprites/batch")
    files = [("files", p.name, p.read_bytes()) for p in pngs]
    body, ctype = _multipart(files)
    print(f"  payload size = {len(body) / 1024 / 1024:.1f} MB")

    t0 = time.time()
    c = _conn(base)
    c.request("POST", url_parts.path + "/api/sprites/batch", body=body,
              headers={"Content-Type": ctype, "x-admin-key": key,
                       "Content-Length": str(len(body))})
    r = c.getresponse()
    raw = r.read()
    c.close()
    if r.status != 200:
        sys.exit(f"batch failed {r.status}: {raw.decode(errors='replace')[:400]}")
    info = json.loads(raw)
    print(f"  uploaded in {time.time() - t0:.1f}s")
    print(f"  job_id        = {info['job_id']}")
    print(f"  files         = {info['files']}")
    print(f"  total sprites = {info['total_sprites']}")
    print()
    print("Server is generating in the background. You can close the laptop now.")
    print(f"Check progress  curl -H 'x-admin-key: $KEY' {base}/api/sprites/jobs/{info['job_id']}")
    print(f"List sprites    curl -H 'x-admin-key: $KEY' {base}/api/sprites/list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
