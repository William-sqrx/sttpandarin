"""One-off fix: strip POS prefix from the two 省 newlessons entries (HSK4
part 12 and part 13) and remove the orphaned 'n. province' words doc.

Dry-run by default; --apply to actually write.

    python fix_sheng.py            # preview
    python fix_sheng.py --apply    # commit
"""

import argparse
import sys
from bson import ObjectId

import db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    lessons = db.lessons_col()
    words = db.words_col()

    fixes = [
        # Part 13: "v. to save, to economize" → "to save, to economize"
        {
            "kind": "newlessons.update",
            "filter": {
                "hskLevel": "4",
                "newWords": {"$elemMatch": {
                    "chinese": "省",
                    "english": "v. to save, to economize",
                }},
            },
            "update": {"$set": {"newWords.$.english": "to save, to economize"}},
            "label": "part 13: strip 'v. '",
        },
        # Part 12: "n. province" → "province"
        {
            "kind": "newlessons.update",
            "filter": {
                "hskLevel": "4",
                "newWords": {"$elemMatch": {
                    "chinese": "省",
                    "english": "n. province",
                }},
            },
            "update": {"$set": {"newWords.$.english": "province"}},
            "label": "part 12: strip 'n. '",
        },
        # Drop the orphan 'n. province' word doc (no longer referenced)
        {
            "kind": "words.delete",
            "filter": {"_id": ObjectId("69eaf2725d5d989924ee179f")},
            "label": "delete orphan words doc 69eaf272...",
        },
    ]

    for fix in fixes:
        if fix["kind"] == "newlessons.update":
            doc = lessons.find_one(fix["filter"], {"_id": 1, "topicIndex": 1})
            if not doc:
                print(f"  SKIP  {fix['label']} — no matching doc")
                continue
            print(f"  WILL  {fix['label']} (topicIndex={doc.get('topicIndex')})")
            if args.apply:
                r = lessons.update_one(fix["filter"], fix["update"])
                print(f"        modified={r.modified_count}")
        elif fix["kind"] == "words.delete":
            doc = words.find_one(fix["filter"], {"chinese": 1, "english": 1, "level": 1})
            if not doc:
                print(f"  SKIP  {fix['label']} — already gone")
                continue
            print(f"  WILL  {fix['label']} (chinese={doc.get('chinese')!r} "
                  f"english={doc.get('english')!r} level={doc.get('level')})")
            if args.apply:
                r = words.delete_one(fix["filter"])
                print(f"        deleted={r.deleted_count}")

    if not args.apply:
        print("\n(dry-run) re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
