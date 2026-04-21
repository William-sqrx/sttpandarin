"""Pinyin helpers.

- `pinyin_options(hanzi)` returns up to MAX_OPTIONS possible whole-word pinyin
  spellings (space-separated, tone marks) using pypinyin heteronym permutations.
- `annotate_with_pinyin(hanzi, pinyin)` produces a TTS-friendly string with
  inline `(pinyin)` hints per syllable — a best-effort nudge for qwen-tts on
  polyphones.
"""

from itertools import product

from pypinyin import Style, lazy_pinyin, pinyin

MAX_OPTIONS = 24


def _tokenize_pinyin(pinyin_str: str) -> list[str]:
    return [t for t in (pinyin_str or "").replace("，", " ").split() if t]


def pinyin_options(hanzi: str, current: str = "") -> list[str]:
    """Return distinct pinyin options for the given hanzi.

    Uses per-character heteronym lookup (bypassing word segmentation) so
    polyphones like 行 surface both 'xíng' and 'háng'. The first entry is
    the current pinyin (if non-empty), then pypinyin's context-aware default,
    then permutations of all per-character readings.
    """
    hanzi = (hanzi or "").strip()
    if not hanzi:
        return [current] if current else []

    # Per-char readings without word-level segmentation.
    per_char: list[list[str]] = []
    for ch in hanzi:
        if not ch.strip():
            continue
        opts = pinyin(ch, style=Style.TONE, heteronym=True, errors="default")
        readings = opts[0] if opts else [ch]
        # Drop dups, cap per-char alternatives.
        seen_c: list[str] = []
        for r in readings:
            if r and r not in seen_c:
                seen_c.append(r)
        per_char.append(seen_c[:4] or [ch])

    # Bail early if permutation count would be excessive.
    total = 1
    for opts in per_char:
        total *= max(1, len(opts))
        if total > MAX_OPTIONS * 4:
            per_char = [[opts[0]] for opts in per_char]
            break

    seen: list[str] = []
    if current:
        seen.append(current)
    default = " ".join(lazy_pinyin(hanzi, style=Style.TONE))
    if default and default not in seen:
        seen.append(default)
    for combo in product(*per_char):
        s = " ".join(combo).strip()
        if s and s not in seen:
            seen.append(s)
        if len(seen) >= MAX_OPTIONS:
            break
    return seen


def annotate_with_pinyin(hanzi: str, pinyin_str: str) -> str:
    """Return hanzi with inline `(pinyin)` annotations per character.

    E.g. annotate_with_pinyin("银行", "yín háng") → "银(yín)行(háng)".
    If tokens don't match length, returns hanzi unchanged.
    """
    tokens = _tokenize_pinyin(pinyin_str)
    if not tokens or len(tokens) != len(hanzi):
        return hanzi
    return "".join(f"{ch}({tok})" for ch, tok in zip(hanzi, tokens))
