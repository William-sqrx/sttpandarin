"""Copy a word's audioBlob from one level to another, matched on chinese.

Usage:
    python copy_audio.py --chinese 给 --from-level 1 --to-level 2           # dry-run
    python copy_audio.py --chinese 给 --from-level 1 --to-level 2 --apply
"""

import argparse
import sys

from bson import Binary

import db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chinese", required=True)
    ap.add_argument("--from-level", type=int, required=True)
    ap.add_argument("--to-level", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    coll = db.words_col()

    src = coll.find_one(
        {"chinese": args.chinese, "level": args.from_level},
        {"chinese": 1, "pinyin": 1, "english": 1, "level": 1, "audioBlob": 1},
    )
    if not src:
        print(f"ERROR: no words doc for chinese={args.chinese!r} level={args.from_level}")
        return 1
    blob = src.get("audioBlob")
    if not blob:
        print(f"ERROR: source doc has no audioBlob ({src['_id']})")
        return 1

    dst = coll.find_one(
        {"chinese": args.chinese, "level": args.to_level},
        {"chinese": 1, "pinyin": 1, "english": 1, "level": 1, "audioBlob": 1},
    )
    if not dst:
        print(f"ERROR: no words doc for chinese={args.chinese!r} level={args.to_level}")
        return 1

    src_bytes = bytes(blob) if isinstance(blob, (bytes, bytearray, Binary)) else None
    src_size = len(src_bytes) if src_bytes else "?"

    print(f"  source : {src['_id']}  level {src['level']}  {src.get('pinyin')!r:<10}  {src.get('english')!r}  ({src_size} bytes)")
    print(f"  dest   : {dst['_id']}  level {dst['level']}  {dst.get('pinyin')!r:<10}  {dst.get('english')!r}")
    print(f"           current dest audio: {'present' if dst.get('audioBlob') else 'missing'}")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to overwrite destination audioBlob.")
        return 0

    coll.update_one(
        {"_id": dst["_id"]},
        {"$set": {"audioBlob": Binary(src_bytes)}},
    )
    print("  ✓ overwrote destination audioBlob")
    return 0


if __name__ == "__main__":
    sys.exit(main())
