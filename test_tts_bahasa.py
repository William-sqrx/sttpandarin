"""Try Alibaba DashScope TTS on Bahasa Indonesia.

Compares qwen3-tts-flash (officially supports Indonesian) vs cosyvoice-v2
(zh/en/ja/ko only — included to hear how it degrades).

Run: python test_tts_bahasa.py
Outputs WAV/MP3 files to ./bahasa_tts_out/
"""

import os
import sys
from pathlib import Path

import dashscope
import requests
from dashscope.audio.qwen_tts import SpeechSynthesizer as QwenTTS
from dashscope.audio.tts_v2 import SpeechSynthesizer as CosyTTS

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
if not API_KEY:
    sys.exit("DASHSCOPE_API_KEY not set")
dashscope.api_key = API_KEY

OUT = Path(__file__).parent / "bahasa_tts_out"
OUT.mkdir(exist_ok=True)

SAMPLES = {
    "greeting": "Selamat pagi! Apa kabar hari ini?",
    "sentence": "Saya sangat senang bisa belajar bahasa Indonesia bersama Anda.",
    "numbers": "Harga buku ini lima belas ribu rupiah.",
}

QWEN_VOICES = ["Cherry", "Serena", "Chelsie", "Ethan", "Neil"]
COSY_VOICES = ["longxiaochun_v2", "longxiaobai_v2", "longwan_v2", "loongstella_v2"]


def run_qwen(label: str, text: str, voice: str) -> None:
    out_path = OUT / f"qwen3flash__{voice}__{label}.wav"
    try:
        resp = QwenTTS.call(
            model="qwen3-tts-flash",
            api_key=API_KEY,
            text=text,
            voice=voice,
        )
        if getattr(resp, "status_code", 200) != 200:
            print(f"  [FAIL] qwen3-flash {voice} {label}: {resp.message}")
            return
        url = resp.output.audio["url"]
        out_path.write_bytes(requests.get(url, timeout=60).content)
        print(f"  [ok]   {out_path.name}")
    except Exception as e:
        print(f"  [FAIL] qwen3-flash {voice} {label}: {e}")


def run_cosy(label: str, text: str, voice: str) -> None:
    out_path = OUT / f"cosyvoice2__{voice}__{label}.mp3"
    try:
        synth = CosyTTS(model="cosyvoice-v2", voice=voice)
        audio = synth.call(text)
        if not audio:
            print(f"  [FAIL] cosy {voice} {label}: empty audio")
            return
        out_path.write_bytes(audio)
        print(f"  [ok]   {out_path.name}")
    except Exception as e:
        print(f"  [FAIL] cosy {voice} {label}: {e}")


def main() -> None:
    print(f"Writing to {OUT}\n")
    for label, text in SAMPLES.items():
        print(f"Sample '{label}': {text}")
        print(" qwen3-tts-flash (officially supports Indonesian):")
        for v in QWEN_VOICES:
            run_qwen(label, text, v)
        print(" cosyvoice-v2 (officially zh/en/ja/ko only):")
        for v in COSY_VOICES:
            run_cosy(label, text, v)
        print()


if __name__ == "__main__":
    main()
