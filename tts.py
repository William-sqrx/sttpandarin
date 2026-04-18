"""qwen-tts synthesis + ffmpeg post-processing (speed, padding, concat)."""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import dashscope
import requests
from dashscope.audio.qwen_tts import SpeechSynthesizer

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY", "")

MODEL_DEFAULT = "qwen-tts"
FEMALE_VOICES = ["Cherry", "Serena", "Chelsie"]
MALE_VOICES = ["Ethan", "Neil"]


class TTSError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise TTSError("ffmpeg not installed on server")


def synthesize_raw(text: str, voice: str, model: str, api_key: str) -> bytes:
    """One qwen-tts call. Returns WAV bytes."""
    if not api_key:
        raise TTSError("DASHSCOPE_API_KEY is not set")
    response = SpeechSynthesizer.call(
        model=model,
        api_key=api_key,
        text=text,
        voice=voice,
    )
    status = getattr(response, "status_code", 200)
    if status != 200:
        raise TTSError(f"qwen-tts error {status}: {response.message}", status_code=status)
    url = response.output.audio["url"]
    wav_bytes = requests.get(url, timeout=60).content
    if not wav_bytes:
        raise TTSError("empty audio from qwen-tts")
    return wav_bytes


def synthesize_with_retry(
    text: str,
    voice: str,
    model: str,
    api_key: str,
    max_retries: int = 4,
    rate_limit_backoff: int = 20,
) -> bytes:
    rl_attempt = 0
    other_attempt = 0
    while True:
        try:
            return synthesize_raw(text, voice, model, api_key)
        except TTSError as e:
            is_rate = e.status_code == 429 or "429" in str(e)
            if is_rate:
                rl_attempt += 1
                wait = min(rate_limit_backoff * (2 ** (rl_attempt - 1)), 300)
                time.sleep(wait)
                continue
            other_attempt += 1
            if other_attempt >= max_retries:
                raise
            time.sleep(2 * other_attempt)


def wav_to_mp3(
    wav_bytes: bytes,
    speed: float = 1.0,
    tail_pad_seconds: float = 0.3,
    bitrate: str = "192k",
) -> bytes:
    """Convert WAV to MP3 with optional speed change + tail padding."""
    _require_ffmpeg()
    filters = []
    if abs(speed - 1.0) > 1e-3:
        filters.append(f"atempo={speed}")
    if tail_pad_seconds > 0:
        filters.append(f"apad=pad_dur={tail_pad_seconds}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src, \
         tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as dst:
        src_path, dst_path = Path(src.name), Path(dst.name)
        src.write(wav_bytes)
    try:
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src_path)]
        if filters:
            cmd += ["-af", ",".join(filters)]
        cmd += ["-ab", bitrate, str(dst_path)]
        subprocess.run(cmd, check=True)
        return dst_path.read_bytes()
    finally:
        src_path.unlink(missing_ok=True)
        dst_path.unlink(missing_ok=True)


def _silence_mp3(duration_seconds: float, bitrate: str = "192k") -> bytes:
    _require_ffmpeg()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as dst:
        dst_path = Path(dst.name)
    try:
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"anullsrc=channel_layout=mono:sample_rate=24000",
                "-t", f"{duration_seconds}",
                "-ab", bitrate, str(dst_path),
            ],
            check=True,
        )
        return dst_path.read_bytes()
    finally:
        dst_path.unlink(missing_ok=True)


def concat_mp3(segments: list[bytes], bitrate: str = "192k") -> bytes:
    """Concat multiple MP3 segments into one."""
    _require_ffmpeg()
    if len(segments) == 1:
        return segments[0]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        list_path = tmp_dir / "list.txt"
        paths = []
        for i, seg in enumerate(segments):
            p = tmp_dir / f"seg_{i:03d}.mp3"
            p.write_bytes(seg)
            paths.append(p)
        list_path.write_text("\n".join(f"file '{p}'" for p in paths))
        out_path = tmp_dir / "out.mp3"
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-c:a", "libmp3lame", "-ab", bitrate, str(out_path),
            ],
            check=True,
        )
        return out_path.read_bytes()


def build_segment(
    pieces: list[tuple[str, str]],
    model: str,
    api_key: str,
    speed: float,
    gap_between_speakers: float,
    repeat: int = 1,
    gap_between_repeats: float = 1.0,
    tail_pad: float = 0.3,
) -> bytes:
    """Build a single MP3 by synthesizing each (text, voice) piece, joining with gaps,
    and repeating the whole joined clip `repeat` times separated by gap_between_repeats.
    """
    rendered = []
    for text, voice in pieces:
        if not text.strip():
            continue
        wav = synthesize_with_retry(text, voice, model, api_key)
        mp3 = wav_to_mp3(wav, speed=speed, tail_pad_seconds=tail_pad)
        rendered.append(mp3)

    if not rendered:
        raise TTSError("no non-empty pieces to render")

    # Interleave speaker gaps
    single_run: list[bytes] = []
    for i, mp3 in enumerate(rendered):
        if i > 0 and gap_between_speakers > 0:
            single_run.append(_silence_mp3(gap_between_speakers))
        single_run.append(mp3)
    single = concat_mp3(single_run)

    # Repeat whole run
    if repeat <= 1:
        return single
    runs: list[bytes] = []
    for i in range(repeat):
        if i > 0 and gap_between_repeats > 0:
            runs.append(_silence_mp3(gap_between_repeats))
        runs.append(single)
    return concat_mp3(runs)
