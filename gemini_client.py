"""Shared google-genai client factory.

Two auth modes, auto-detected at call time:

  * Developer API  — set ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``). This is an
    API key minted in Google AI Studio (https://aistudio.google.com/apikey)
    under ANY Google account. Use this when the app runs under a plain Google
    account that has no Cloud project / service-account JSON. Veo video gen on
    this path needs billing enabled on the account.

  * Vertex AI      — no key set; falls back to a service-account JSON pointed
    at by ``GOOGLE_APPLICATION_CREDENTIALS``, targeting ``GOOGLE_CLOUD_PROJECT``.

So: set ``GEMINI_API_KEY`` on Render and both Veo (fish anims) and Gemini image
gen run under that key — no service-account JSON required.
"""
from __future__ import annotations

import os


def api_key() -> str:
    """The Gemini Developer API key, if configured (else empty string)."""
    return (os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip())


def using_api_key() -> bool:
    """True when a Developer API key is set (so we skip Vertex/service-account)."""
    return bool(api_key())


def new_client(project: str = "", location: str = ""):
    """Build a google-genai Client. Prefers the Developer API (api_key) when a
    key is set; otherwise Vertex AI with the given project/location."""
    from google import genai  # lazy — SDK is heavy and optional locally
    key = api_key()
    if key:
        return genai.Client(api_key=key)
    return genai.Client(vertexai=True, project=project, location=location)
