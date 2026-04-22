"""Strip leading part-of-speech prefixes (e.g. "n. ", "v. ", "n./v. ") from
word definitions.

Targets the same MongoDB the webapp uses. Cleans BOTH collections:
- words.english
- newlessons.newWords[*].english

Dry-run by default. Pass --apply to actually write changes.

Usage:
    python clean_pos_prefixes.py            # preview only
    python clean_pos_prefixes.py --apply    # write to DB
    python clean_pos_prefixes.py --apply --collection words   # only one
"""

import argparse
import re
import sys

from bson import ObjectId
from pymongo import UpdateOne

import db


# Common part-of-speech abbreviations seen in HSK / dictionary materials.
# Order doesn't matter — they're alternated in a regex.
POS_TAGS = [
    "n", "v", "adj", "adv", "prep", "conj", "pron", "num", "m",
    "intj", "aux", "p", "onom", "art", "abbr", "int", "id", "excl",
    "mw", "cl", "interj", "interjection", "particle", "ono", "idiom",
    "phr", "phrase", "abbrev", "vi", "vt", "v.i", "v.t",
]
_TAG_RE = "|".join(re.escape(t) for t in sorted(POS_TAGS, key=len, reverse=True))

# Matches one or more POS tags at the start, separated by `/`, `;`, `,`, `&`,
# or just whitespace. Each tag ends with a period and optional spaces.
POS_PATTERN = re.compile(rf"^\s*(?:(?:{_TAG_RE})\.\s*[/;,&]?\s*)+", re.IGNORECASE)


def strip_pos(text: str) -> str:
    if not text:
        return text
    cleaned = POS_PATTERN.sub("", text).strip()
    return cleaned or text  # don't blank out if the whole thing was POS-like


def clean_words(apply: bool) -> tuple[int, int]:
    coll = db.words_col()
    cursor = coll.find({}, {"chinese": 1, "english": 1})
    ops: list[UpdateOne] = []
    changed = 0
    total = 0
    for doc in cursor:
        total += 1
        before = doc.get("english") or ""
        after = strip_pos(before)
        if after != before:
            changed += 1
            print(f"  words[{doc['_id']}]  {doc.get('chinese','?')!r:8}  {before!r}  →  {after!r}")
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"english": after}}))
    if apply and ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"  → wrote {res.modified_count} words doc(s)")
    return total, changed


def clean_lessons(apply: bool) -> tuple[int, int]:
    coll = db.lessons_col()
    cursor = coll.find({}, {"newWords.english": 1})
    ops: list[UpdateOne] = []
    word_total = 0
    changed = 0
    for doc in cursor:
        new_words = doc.get("newWords") or []
        per_doc_set: dict[str, str] = {}
        for i, w in enumerate(new_words):
            word_total += 1
            before = w.get("english") or ""
            after = strip_pos(before)
            if after != before:
                changed += 1
                print(f"  lesson[{doc['_id']}].newWords[{i}]  {before!r}  →  {after!r}")
                per_doc_set[f"newWords.{i}.english"] = after
        if per_doc_set:
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": per_doc_set}))
    if apply and ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"  → wrote {res.modified_count} lesson doc(s)")
    return word_total, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write changes to MongoDB (default: dry-run)")
    ap.add_argument("--collection", choices=["words", "lessons", "both"],
                    default="both", help="which collection to clean")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] cleaning collection(s): {args.collection}")
    print(f"  DB: {db.DB_NAME}\n")

    if args.collection in ("words", "both"):
        print("== words ==")
        total, changed = clean_words(args.apply)
        print(f"  scanned {total}, changed {changed}\n")

    if args.collection in ("lessons", "both"):
        print("== newlessons.newWords ==")
        total, changed = clean_lessons(args.apply)
        print(f"  scanned {total} embedded words, changed {changed}\n")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
