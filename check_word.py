"""Diagnose why a word shows 'not found in words collection' on the HSK Browser.
Compares newlessons triple vs words triple to spot pinyin/english drift.

Usage:
    python check_word.py --chinese 省 --level 4
"""

import argparse
import sys

import db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chinese", required=True)
    ap.add_argument("--level", type=int)
    args = ap.parse_args()

    print(f"\n=== words collection — chinese={args.chinese!r} ===")
    wfilter = {"chinese": args.chinese}
    if args.level is not None:
        wfilter["level"] = args.level
    docs = list(db.words_col().find(
        wfilter,
        {"chinese": 1, "pinyin": 1, "english": 1, "level": 1, "audioBlob": 1},
    ))
    if not docs:
        print("  (no rows)")
    else:
        for d in docs:
            has_audio = "yes" if d.get("audioBlob") else "no"
            print(
                f"  _id={d['_id']} level={d.get('level')!r} "
                f"pinyin={d.get('pinyin')!r} english={d.get('english')!r} audio={has_audio}"
            )

    print(f"\n=== newlessons — newWords containing chinese={args.chinese!r} ===")
    nq = {"newWords.chinese": args.chinese}
    if args.level is not None:
        nq["hskLevel"] = str(args.level)
    cur = db.lessons_col().find(
        nq,
        {
            "hskLevel": 1, "topicTitle": 1, "topicIndex": 1,
            "newWords.chinese": 1, "newWords.pinyin": 1, "newWords.english": 1,
        },
    )
    found = 0
    for lesson in cur:
        for w in lesson.get("newWords", []):
            if w.get("chinese") == args.chinese:
                found += 1
                print(
                    f"  HSK{lesson.get('hskLevel')} part {lesson.get('topicIndex')} "
                    f"({lesson.get('topicTitle')!r}) — "
                    f"pinyin={w.get('pinyin')!r} english={w.get('english')!r}"
                )
    if not found:
        print("  (not found in any newlessons doc)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
