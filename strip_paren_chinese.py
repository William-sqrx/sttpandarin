"""Strip parenthetical alternates from chinese fields in both collections.

- newlessons.newWords[*].chinese
- words.chinese

'没有（没)' → '没有', '锯(子)' → '锯'

Dry-run by default. --apply to write.
"""

import argparse
import re
import sys

from pymongo import UpdateOne

import db

PAREN_RE = re.compile(r"[（(][^)）]*[)）]")


def strip_parens(s: str) -> str:
    if not s:
        return s
    return PAREN_RE.sub("", s).strip()


def clean_words(apply: bool) -> tuple[int, int]:
    coll = db.words_col()
    ops: list[UpdateOne] = []
    total = changed = 0
    for doc in coll.find({}, {"chinese": 1}):
        total += 1
        before = doc.get("chinese") or ""
        after = strip_parens(before)
        if after and after != before:
            changed += 1
            print(f"  words[{doc['_id']}]  {before!r}  →  {after!r}")
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"chinese": after}}))
    if apply and ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"  → wrote {res.modified_count} words doc(s)")
    return total, changed


def clean_lessons(apply: bool) -> tuple[int, int]:
    coll = db.lessons_col()
    ops: list[UpdateOne] = []
    total = changed = 0
    for doc in coll.find({}, {"newWords.chinese": 1}):
        new_words = doc.get("newWords") or []
        per_doc: dict[str, str] = {}
        for i, w in enumerate(new_words):
            total += 1
            before = w.get("chinese") or ""
            after = strip_parens(before)
            if after and after != before:
                changed += 1
                print(f"  lesson[{doc['_id']}].newWords[{i}]  {before!r}  →  {after!r}")
                per_doc[f"newWords.{i}.chinese"] = after
        if per_doc:
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": per_doc}))
    if apply and ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"  → wrote {res.modified_count} lesson doc(s)")
    return total, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--collection", choices=["words", "lessons", "both"], default="both")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] stripping parens from chinese fields")
    print(f"  DB: {db.DB_NAME}\n")

    if args.collection in ("words", "both"):
        print("== words ==")
        t, c = clean_words(args.apply)
        print(f"  scanned {t}, changed {c}\n")

    if args.collection in ("lessons", "both"):
        print("== newlessons.newWords ==")
        t, c = clean_lessons(args.apply)
        print(f"  scanned {t}, changed {c}\n")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
