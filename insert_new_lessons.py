"""Insert 4 new lessons into newlessons at their textbook-correct (0-indexed)
topicIndex slots. The DB already follows a `topicIndex = textbook_chapter - 1`
convention and these four slots happen to be empty gaps, so no renumbering
is needed.

Insertions:
- HSK 5 topicIndex 12 (textbook ch.13)  放眼世界 (Seeing the World)
- HSK 5 topicIndex 15 (textbook ch.16)  体重与节食 (Weight and Diet)
- HSK 6 topicIndex  1 (textbook ch.2)   父母之爱 (Love of parents)
- HSK 6 topicIndex  3 (textbook ch.4)   完美的胜利 (A perfect victory)

The new newWords docs carry chinese/pinyin/english (POS-stripped) + sentenceIndex,
no audioBlob — audio lives on the `words` collection, which the HSK Browser
and the app's vocab flow both read from.

Refuses to overwrite an existing lesson at the same slot.

Dry-run by default. --apply to actually write.
"""

import argparse
import sys

import db
from clean_pos_prefixes import strip_pos
from upload_hsk6_lessons import (
    LESSON_HSK5_L13,
    LESSON_HSK5_L16,
    LESSON_HSK6_L2,
    LESSON_HSK6_L4,
)

# (hskLevel, topicIndex, topicTitle, englishTitle, words)
INSERTIONS = [
    ("5", 12, "放眼世界", "Seeing the World", LESSON_HSK5_L13),
    ("5", 15, "体重与节食", "Weight and Diet", LESSON_HSK5_L16),
    ("6",  1, "父母之爱", "Love of parents", LESSON_HSK6_L2),
    ("6",  3, "完美的胜利", "A perfect victory", LESSON_HSK6_L4),
]


def build_new_words(word_list: list[tuple[str, str, str]]) -> list[dict]:
    out = []
    for i, (chinese, pinyin, english_raw) in enumerate(word_list):
        out.append({
            "chinese": chinese,
            "pinyin": pinyin,
            "english": strip_pos(english_raw),
            "sentenceIndex": i + 1,
        })
    return out


def insert_lessons(apply: bool) -> int:
    coll = db.lessons_col()
    failures = 0
    for level, idx, title, english_title, words in INSERTIONS:
        existing = coll.find_one(
            {"hskLevel": level, "topicIndex": idx},
            {"topicTitle": 1},
        )
        if existing:
            print(f"    [SKIP] HSK {level} #{idx}: slot occupied by "
                  f"{existing.get('topicTitle')!r} — refusing to overwrite")
            failures += 1
            continue

        new_words = build_new_words(words)
        print(f"    [NEW ] HSK {level} #{idx:2d}  {title}  "
              f"({english_title})  ({len(new_words)} words)")
        if apply:
            coll.insert_one({
                "hskLevel": level,
                "topicIndex": idx,
                "topicTitle": title,
                "englishTitle": english_title,
                "newWords": new_words,
                "conversation": [],
            })
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="insert new lesson docs (default: dry-run)")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] inserting 4 new newlessons docs at empty topicIndex slots")
    print(f"  DB: {db.DB_NAME}\n")

    failures = insert_lessons(args.apply)

    if failures:
        print(f"\n⚠ {failures} insertion(s) skipped due to occupied slots.")
    if not args.apply:
        print("\nDry-run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
