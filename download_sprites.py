#!/usr/bin/env python3
"""Download all completed sprite sheets from the chinesely backend.

Usage:
  SPRITE_ADMIN_KEY=a python3 download_sprites.py [output_folder] [--url URL]

Defaults:
  output_folder = ./sprites_out
  url           = https://chinesely-backend-1.onrender.com

Pure stdlib — no third-party deps.
"""
import argparse
import getpass
import json
import os
import ssl
import sys
from collections import defaultdict
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_URL = "https://chinesely-backend-1.onrender.com"


def _conn(base: str):
    u = urlparse(base)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)
    if u.scheme == "https":
        return HTTPSConnection(host, port, timeout=120,
                               context=ssl.create_default_context())
    return HTTPConnection(host, port, timeout=120)


def get_json(base: str, path: str, key: str):
    c = _conn(base)
    c.request("GET", path, headers={"x-admin-key": key})
    r = c.getresponse()
    data = json.loads(r.read())
    c.close()
    if r.status != 200:
        sys.exit(f"GET {path} failed {r.status}: {data}")
    return data


def get_bytes(base: str, path: str, key: str) -> bytes:
    c = _conn(base)
    c.request("GET", path, headers={"x-admin-key": key})
    r = c.getresponse()
    data = r.read()
    c.close()
    if r.status != 200:
        sys.exit(f"GET {path} failed {r.status}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", nargs="?", default="sprites_out",
                    help="folder to save PNG sprite sheets")
    ap.add_argument("--url", default=DEFAULT_URL, help="backend base URL")
    ap.add_argument("--key", default=os.getenv("SPRITE_ADMIN_KEY"),
                    help="admin key (or set SPRITE_ADMIN_KEY env var)")
    args = ap.parse_args()

    key = args.key or getpass.getpass("SPRITE_ADMIN_KEY: ")
    base = args.url.rstrip("/")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Fetching sprite list from {base} …")
    data = get_json(base, "/api/sprites/list", key)
    sprites = data.get("sprites", [])
    if not sprites:
        print("No sprites found.")
        return 0

    print(f"Found {len(sprites)} sprite(s). Downloading …\n")

    # Track how many times each base name appears so we can number duplicates
    name_count = defaultdict(int)
    for s in reversed(sprites):  # oldest first
        stem = Path(s["name"]).stem
        name_count[stem] += 1
        idx = name_count[stem]
        suffix = f"_{idx}" if name_count[stem] > 1 else ""
        # We'll assign filenames on a second pass once we know total counts

    # Reset and do the actual download in chronological order (list is newest-first)
    ordered = list(reversed(sprites))
    seen = defaultdict(int)
    for s in ordered:
        stem = Path(s["name"]).stem
        seen[stem] += 1
        total_for_name = name_count[stem]
        idx = seen[stem]
        # If there's only one sheet for this fish, don't add a number
        fname = f"{stem}_{idx}.png" if total_for_name > 1 else f"{stem}.png"
        dest = out / fname
        print(f"  [{idx}/{total_for_name}] {s['name']} → {fname} "
              f"({s['cols']}×{s['rows']} cols×rows) … ", end="", flush=True)
        img = get_bytes(base, f"/api/sprites/{s['id']}/image", key)
        dest.write_bytes(img)
        print(f"{len(img) // 1024} KB")

    print(f"\nDone — {len(sprites)} sheet(s) saved to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
