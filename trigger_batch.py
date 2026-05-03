#!/usr/bin/env python3
"""Tiny local trigger — kicks off (or checks/stops) the prod fish-anim
batch. The actual generation runs on the Render webapp; this script
just sends one HTTP request and exits.

Env var (set in webapp/.env or your shell):
  BATCH_UPLOAD_URL   prod base URL (e.g. https://chinesely-tts.onrender.com)

Usage:
  python3 trigger_batch.py [start|status|stop]   # default: start
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def main() -> int:
    url = os.getenv("BATCH_UPLOAD_URL", "").strip().rstrip("/")
    if not url:
        sys.exit("set BATCH_UPLOAD_URL (e.g. https://chinesely-tts.onrender.com)")

    action = (sys.argv[1] if len(sys.argv) > 1 else "start").lower()
    if action not in ("start", "stop", "status"):
        sys.exit("action must be: start | stop | status")

    endpoint = f"{url}/api/fishanims/batch/{action}"
    method = "POST" if action in ("start", "stop") else "GET"
    req = urllib.request.Request(endpoint, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            print(f"{r.status} {endpoint}")
            try:
                print(json.dumps(json.loads(body), indent=2))
            except Exception:  # noqa: BLE001
                print(body)
    except urllib.error.HTTPError as e:
        print(f"{e.code} {endpoint}")
        print(e.read().decode())
        return 1
    except urllib.error.URLError as e:
        print(f"connection error: {e}")
        return 1

    if action == "start":
        gallery = f"{url}/fishanims"
        print(f"\nopen {gallery} to watch progress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
