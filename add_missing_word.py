"""Insert a single missing word into the `words` collection with qwen-tts audio.

Usage:
    DASHSCOPE_API_KEY=sk-... python add_missing_word.py \
        --chinese 省 --pinyin "shěng" --english "province; to save" --level 4

    DASHSCOPE_API_KEY=sk-... python add_missing_word.py \
        --chinese 省 --pinyin "shěng" --english "province; to save" --level 4 --apply

Refuses to overwrite an existing entry (by chinese + level). Dry-run by default;
pass --apply to actually insert. Reuses the project's tts.py + db.py so audio
matches the rest of the words collection (same voice, same anti-clip filler).
"""

import argparse
import os
import sys

from bson import Binary

import db
import tts


VOICE = "Serena"
MODEL = "qwen3-tts-flash"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chinese", required=True)
    ap.add_argument("--pinyin", required=True)
    ap.add_argument("--english", required=True)
    ap.add_argument("--level", type=int, required=True, choices=[1, 2, 3, 4, 5, 6])
    ap.add_argument("--apply", action="store_true",
                    help="actually insert (default: dry-run)")
    args = ap.parse_args()

    # Defaults to the same key baked into generate_hsk_tts.py so the script
    # is copy-paste runnable. Override with DASHSCOPE_API_KEY env var if needed.
    api_key = os.getenv(
        "DASHSCOPE_API_KEY",
        "sk-7dda280dec2743599ec05424a19c905f",
    )

    coll = db.words_col()

    existing = coll.find_one(
        {"chinese": args.chinese, "level": args.level},
        {"_id": 1, "pinyin": 1, "english": 1},
    )
    if existing:
        print(
            f"already exists: _id={existing['_id']} "
            f"pinyin={existing.get('pinyin')!r} english={existing.get('english')!r}"
        )
        return 0

    print(
        f"will insert: chinese={args.chinese!r} pinyin={args.pinyin!r} "
        f"english={args.english!r} level={args.level}"
    )

    print("generating audio via qwen-tts ...")
    mp3_bytes = tts.synthesize_piece_mp3(
        text=args.chinese,
        voice=VOICE,
        model=MODEL,
        api_key=api_key,
    )
    print(f"  ok, {len(mp3_bytes)} bytes")

    if not args.apply:
        print("\n(dry-run) re-run with --apply to actually insert.")
        return 0

    doc = {
        "chinese": args.chinese,
        "pinyin": args.pinyin,
        "english": args.english,
        "level": args.level,
        "audioBlob": Binary(mp3_bytes),
    }
    result = coll.insert_one(doc)
    print(f"inserted _id={result.inserted_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
