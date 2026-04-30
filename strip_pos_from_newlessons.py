"""Strip POS prefixes (n., v., adj., etc.) from every newlessons.newWords.english.

Brings newlessons in line with the words collection (which is already
POS-stripped on insert via insert_new_lessons.py → strip_pos()).

Dry-run by default; --apply to actually update.

Usage:
    python strip_pos_from_newlessons.py
    python strip_pos_from_newlessons.py --apply
"""

import argparse
import sys

import db
from clean_pos_prefixes import strip_pos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually update (default: dry-run)")
    args = ap.parse_args()

    coll = db.lessons_col()
    cur = coll.find({}, {"hskLevel": 1, "topicIndex": 1, "newWords": 1})

    total_words = 0
    drift = []  # (hskLevel, topicIndex, idx, chinese, before, after)

    for lesson in cur:
        new_words = lesson.get("newWords") or []
        for i, w in enumerate(new_words):
            total_words += 1
            before = (w.get("english") or "").strip()
            after = strip_pos(before)
            if before != after:
                drift.append((
                    lesson.get("hskLevel"),
                    lesson.get("topicIndex"),
                    i,
                    w.get("chinese"),
                    before,
                    after,
                ))

    print(f"\nScanned {total_words} newWords across all lessons.")
    print(f"Found {len(drift)} entries with POS prefix to strip.\n")

    for hsk, idx, i, ch, before, after in drift[:30]:
        print(f"  HSK{hsk} part {idx} #{i:>2}  {ch}: {before!r} → {after!r}")
    if len(drift) > 30:
        print(f"  ... and {len(drift) - 30} more")

    if not args.apply:
        print("\n(dry-run) re-run with --apply to actually update.")
        return 0

    print("\nApplying updates ...")
    n = 0
    for hsk, idx, i, ch, before, after in drift:
        result = coll.update_one(
            {"hskLevel": hsk, "topicIndex": idx},
            {"$set": {f"newWords.{i}.english": after}},
        )
        if result.modified_count:
            n += 1
    print(f"Updated {n} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
