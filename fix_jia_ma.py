"""One-off fix: correct two HSK 1 word definitions.

  家 "family" → "house"
  吗 "used at the end of last sentence" → "question particle for yes-no questions"

Updates BOTH the flat `words` collection and any `newlessons.newWords[*]`
embedded copies so the lesson view and the SRS source match.

Dry-run by default; --apply to actually write.

    python fix_jia_ma.py            # preview
    python fix_jia_ma.py --apply    # commit
"""

import argparse
import sys

import db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    lessons = db.lessons_col()
    words = db.words_col()

    fixes = [
        # ── 家 ───────────────────────────────────────────────────────────────
        {
            "kind": "words.update",
            "filter": {"chinese": "家", "english": "family"},
            "update": {"$set": {"english": "house"}},
            "label": "words: 家 'family' → 'house'",
        },
        {
            "kind": "newlessons.update",
            "filter": {
                "newWords": {"$elemMatch": {
                    "chinese": "家",
                    "english": "family",
                }},
            },
            "update": {"$set": {"newWords.$.english": "house"}},
            "label": "newlessons: 家 'family' → 'house'",
        },
        # ── 吗 (HSK 1 only — HSK 3 entry is a different particle, leave alone) ─
        {
            "kind": "words.update",
            "filter": {
                "chinese": "吗",
                "level": 1,
                "english": "(used at the end of a question)",
            },
            "update": {
                "$set": {"english": "question particle for yes-no questions"},
            },
            "label": "words: 吗 HSK1 → 'question particle for yes-no questions'",
        },
        {
            "kind": "newlessons.update",
            "filter": {
                "hskLevel": "1",
                "newWords": {"$elemMatch": {
                    "chinese": "吗",
                    "english": "(used at the end of a question)",
                }},
            },
            "update": {
                "$set": {
                    "newWords.$.english": "question particle for yes-no questions",
                },
            },
            "label": "newlessons: 吗 HSK1 → 'question particle for yes-no questions'",
        },
    ]

    for fix in fixes:
        if fix["kind"] == "words.update":
            doc = words.find_one(fix["filter"], {"_id": 1, "level": 1, "pinyin": 1})
            if not doc:
                print(f"  SKIP  {fix['label']} — no matching doc")
                continue
            print(f"  WILL  {fix['label']} (level={doc.get('level')}, pinyin={doc.get('pinyin')!r})")
            if args.apply:
                r = words.update_many(fix["filter"], fix["update"])
                print(f"        modified={r.modified_count}")
        elif fix["kind"] == "newlessons.update":
            n = lessons.count_documents(fix["filter"])
            if n == 0:
                print(f"  SKIP  {fix['label']} — no matching docs")
                continue
            print(f"  WILL  {fix['label']} ({n} lesson doc(s))")
            if args.apply:
                r = lessons.update_many(fix["filter"], fix["update"])
                print(f"        modified={r.modified_count}")

    if not args.apply:
        print("\n(dry-run) re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
