#!/usr/bin/env python3
"""One-shot uploader: send every PNG in a folder to the deployed Fish Studio
batch endpoint, then exit. The server's single worker thread generates 3
sprite sheets per file sequentially, so this laptop can be closed once the
script returns.

Usage:
  APP_PASSWORD=xxx python3 batch_fish_upload.py [folder] [--url URL]

Defaults:
  folder = ../chinesely_app/chinesely-frontend/src/assets/images/test_fish
  url    = https://chinesely-tts.onrender.com

Pure stdlib — no `requests` dependency.
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
from urllib.parse import urlencode, urlparse


DEFAULT_URL = "https://chinesely-tts.onrender.com"
DEFAULT_DIR = (Path(__file__).resolve().parent.parent
               / "chinesely_app" / "chinesely-frontend"
               / "src" / "assets" / "images" / "test_fish")


def _conn(url: str):
    u = urlparse(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)
    if u.scheme == "https":
        return HTTPSConnection(host, port, timeout=300, context=ssl.create_default_context())
    return HTTPConnection(host, port, timeout=300)


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
    ap.add_argument("--url", default=DEFAULT_URL, help="webapp base URL")
    ap.add_argument("--password", default=os.getenv("APP_PASSWORD"),
                    help="login password (or set APP_PASSWORD env var)")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")
    pngs = sorted(folder.glob("*.png"))
    if not pngs:
        sys.exit(f"no .png files in {folder}")
    pw = args.password or getpass.getpass("APP_PASSWORD: ")

    base = args.url.rstrip("/")
    url_parts = urlparse(base)

    # ── login ─────────────────────────────────────────────────────────────
    print(f"login → {base}")
    body = urlencode({"password": pw}).encode()
    c = _conn(base)
    c.request("POST", url_parts.path + "/login", body=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    raw = r.read()
    cookie = None
    for h, v in r.getheaders():
        if h.lower() == "set-cookie" and v.startswith("chinesely_session="):
            cookie = v.split(";", 1)[0]
            break
    c.close()
    loc = r.getheader("Location") or ""
    if r.status != 303 or "error" in loc or not cookie:
        sys.exit(f"login failed: status={r.status} location={loc!r}")

    # ── batch upload ──────────────────────────────────────────────────────
    print(f"submitting {len(pngs)} file(s) → /api/sprite/batch")
    files = [("files", p.name, p.read_bytes()) for p in pngs]
    body, ctype = _multipart(files)
    print(f"  payload size = {len(body) / 1024 / 1024:.1f} MB")

    t0 = time.time()
    c = _conn(base)
    c.request("POST", url_parts.path + "/api/sprite/batch", body=body,
              headers={"Content-Type": ctype, "Cookie": cookie,
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
    print(f"Check progress at  {base}/api/sprite/jobs/{info['job_id']}")
    print(f"Browse results at  {base}  (Fish Studio tab)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
