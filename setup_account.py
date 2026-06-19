#!/usr/bin/env python3
"""Point the webapp's Veo (fish anims) + Gemini (image gen) features at a
Google account — in one command.

    RENDER_API_KEY=rnd_xxx  python setup_account.py  AQ.xxxxKEYxxxx

What "the account" means here
-----------------------------
Google has NO API that turns an email + password into a usable credential, so
this takes the account's **Gemini API key**, not its password. Make the key
ONCE in a browser, signed in as that account:

    https://aistudio.google.com/apikey  →  Create API key  →  copy "AQ…"/"AIza…"

(Veo video generation also needs **billing enabled** on that account.)
After that, hand the key to this script and it does the rest.

What it does
------------
  1. Validate the key against the Gemini API; require Veo + a Gemini image model.
  2. Set GEMINI_API_KEY on the Render service        (Render API).
  3. Trigger a redeploy so it takes effect.
  4. Optionally (--local) write it into ./.env for local runs too.

Args / env
----------
    <api_key>                positional, or env GEMINI_API_KEY, else prompted.
    RENDER_API_KEY           required for the Render steps (rnd_… token).
    --service NAME           Render service name        (default: chinesely-tts)
    --local                  also write the key into ./.env
    --no-deploy              set the env var but don't trigger a redeploy
    --skip-render            only validate (and --local); touch nothing on Render
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
RENDER_API = "https://api.render.com/v1"
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# Auto-load ./.env so GEMINI_API_KEY / RENDER_API_KEY don't need re-pasting.
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    pass


def _die(msg: str, code: int = 1) -> "None":
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


# ----- 1. validate the Gemini key -------------------------------------------

def validate_key(api_key: str) -> None:
    """Confirm the key authenticates and can see Veo + a Gemini image model.
    Exits non-zero with a clear reason if not."""
    try:
        r = requests.get(
            GEMINI_MODELS_URL,
            params={"key": api_key, "pageSize": 200},
            timeout=30,
        )
    except requests.RequestException as e:
        _die(f"could not reach the Gemini API: {e}")
    if r.status_code != 200:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            detail = r.text
        _die(f"key rejected by Gemini API (HTTP {r.status_code}): {detail}")

    names = [m.get("name", "") for m in r.json().get("models", [])]
    veo = [n for n in names if "veo" in n.lower()]
    img = [n for n in names if "image" in n.lower() or "imagen" in n.lower()]
    if not veo:
        _die("key works but has NO Veo model access — fish anims won't generate. "
             "Enable the Veo/Generative models for this account.")
    print(f"✓ key valid — {len(names)} models visible")
    print(f"  Veo:   {', '.join(sorted(n.split('/')[-1] for n in veo))}")
    print(f"  image: {', '.join(sorted(n.split('/')[-1] for n in img)) or '(none)'}")
    if not img:
        print("  ⚠ no Gemini image model visible — /imagegen may not work "
              "(fish anims still will).")


# ----- 2/3. push to Render ---------------------------------------------------

def _render_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def find_service(token: str, name: str) -> str:
    r = requests.get(f"{RENDER_API}/services", headers=_render_headers(token),
                     params={"name": name, "limit": 50}, timeout=30)
    if r.status_code != 200:
        _die(f"Render list services failed (HTTP {r.status_code}): {r.text}")
    for row in r.json():
        svc = row.get("service", row)
        if svc.get("name") == name:
            print(f"✓ found Render service '{name}' → {svc.get('id')}")
            return svc["id"]
    _die(f"no Render service named '{name}' on this account "
         f"(check --service / the RENDER_API_KEY owner)")


def set_env_var(token: str, service_id: str, key: str, value: str) -> None:
    r = requests.put(
        f"{RENDER_API}/services/{service_id}/env-vars/{key}",
        headers=_render_headers(token),
        json={"value": value},
        timeout=30,
    )
    if r.status_code not in (200, 201):
        _die(f"setting {key} on Render failed (HTTP {r.status_code}): {r.text}")
    print(f"✓ set {key} on the service")


def trigger_deploy(token: str, service_id: str) -> None:
    r = requests.post(f"{RENDER_API}/services/{service_id}/deploys",
                      headers=_render_headers(token), json={}, timeout=30)
    if r.status_code not in (200, 201):
        _die(f"triggering deploy failed (HTTP {r.status_code}): {r.text}")
    dep = r.json()
    print(f"✓ redeploy triggered → deploy {dep.get('id', '(id?)')} "
          f"(watch it in the Render dashboard)")


# ----- 4. local .env ---------------------------------------------------------

def write_local_env(key_name: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                stripped = line.lstrip("# ").rstrip("\n")
                if stripped.startswith(f"{key_name}="):
                    lines.append(f"{key_name}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key_name}={value}\n")
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)
    print(f"✓ wrote {key_name} into {ENV_FILE} (gitignored)")


# ----- main ------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Wire a Google/Gemini API key into "
                                             "the webapp on Render.")
    ap.add_argument("api_key", nargs="?", help="Gemini API key (or env GEMINI_API_KEY)")
    ap.add_argument("--service", default=os.getenv("RENDER_SERVICE", "chinesely-tts"))
    ap.add_argument("--local", action="store_true", help="also write ./.env")
    ap.add_argument("--no-deploy", action="store_true", help="don't redeploy")
    ap.add_argument("--skip-render", action="store_true",
                    help="validate only (+ --local); don't touch Render")
    args = ap.parse_args()

    api_key = (args.api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        try:
            api_key = input("Paste the Gemini API key: ").strip()
        except EOFError:
            api_key = ""
    if not api_key:
        _die("no API key given (positional arg, env GEMINI_API_KEY, or prompt)")

    print("→ validating key…")
    validate_key(api_key)

    if args.local:
        write_local_env("GEMINI_API_KEY", api_key)

    if args.skip_render:
        print("• --skip-render: leaving Render untouched. Done.")
        return

    token = os.getenv("RENDER_API_KEY", "").strip()
    if not token:
        _die("RENDER_API_KEY not set — get a token at Render → Account Settings → "
             "API Keys, then re-run with  RENDER_API_KEY=rnd_… (or pass --skip-render).")

    print(f"→ updating Render service '{args.service}'…")
    service_id = find_service(token, args.service)
    set_env_var(token, service_id, "GEMINI_API_KEY", api_key)
    if args.no_deploy:
        print("• --no-deploy: env var set; redeploy skipped (it applies on next deploy).")
    else:
        trigger_deploy(token, service_id)
    print("\n✓ all set — fish anims at /fishanims will run under this account.")


if __name__ == "__main__":
    main()
