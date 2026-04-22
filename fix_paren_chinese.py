"""Correct the over-aggressive paren stripping from strip_paren_chinese.py.

The previous pass removed both the parens AND the content inside them, which
was wrong for the majority of textbook entries where the parens wrap an
OPTIONAL suffix that is still part of the real word (e.g. 夏(天) → 夏天).

This script takes each affected doc and sets `chinese` to the decided final
value directly. Idempotent: if the value already matches, no write happens.

Decisions (per user confirmation):
- EXPAND → remove only the parens, keep the content (e.g. 夏(天) → 夏天)
- STRIP  → remove parens AND content (e.g. 没有（没) → 没有)

Dry-run by default. --apply to write.
"""

import argparse
import sys

from bson import ObjectId
from pymongo import UpdateOne

import db

# (lesson_id, newWords_index, final_chinese)
LESSON_FIXES: list[tuple[str, int, str]] = [
    ("687766a03983bae08a870908",  9, "没有"),        # STRIP
    ("687767a0790667f0a7af2524",  7, "秋天"),        # EXPAND
    ("687767a0790667f0a7af2547", 14, "极了"),        # EXPAND
    ("687767a1790667f0a7af256a", 16, "笔记本电脑"),    # EXPAND
    ("687767a1790667f0a7af25de", 12, "聊天"),        # STRIP
    ("687767a1790667f0a7af262b",  7, "春天"),        # EXPAND
    ("687767a1790667f0a7af262b",  9, "夏天"),        # EXPAND
    ("687767a2790667f0a7af2758", 10, "冬天"),        # EXPAND
    ("68776aced71104b5a95f2867", 12, "值得"),        # EXPAND
    ("68776acfd71104b5a95f29c8",  8, "勺子"),        # EXPAND
    ("68776db6732cbf7e63cc5ae2", 16, "银子"),        # EXPAND
    ("68776db6732cbf7e63cc5b23", 17, "治疗"),        # EXPAND
    ("68776db6732cbf7e63cc5b23", 22, "躲藏"),        # EXPAND
    ("68776db6732cbf7e63cc5b66",  3, "屋子"),        # EXPAND
    ("68776db7732cbf7e63cc5be1",  4, "大象"),        # EXPAND
    ("68776db7732cbf7e63cc5be1", 32, "尽力"),        # EXPAND
    ("68776db7732cbf7e63cc5c27",  7, "骨头"),        # EXPAND
    ("68776db7732cbf7e63cc5c27", 16, "代替"),        # EXPAND
    ("68776db7732cbf7e63cc5c27", 29, "盆子"),        # EXPAND
    ("68776db7732cbf7e63cc5c27", 35, "射击"),        # EXPAND
    ("68776db8732cbf7e63cc5ca6",  4, "蹄子"),        # EXPAND
    ("68776db9732cbf7e63cc5ce0", 19, "慌张"),        # EXPAND
    ("68776dbb732cbf7e63cc5e7d", 25, "繁体字"),      # EXPAND
    ("68776dbb732cbf7e63cc5e7d", 26, "简体字"),      # EXPAND
    ("68776dbc732cbf7e63cc5f60",  6, "使劲"),        # STRIP
    ("68776dbd732cbf7e63cc5fd5",  0, "象棋"),        # EXPAND
    ("68776dbd732cbf7e63cc600f", 13, "初中"),        # STRIP
    ("68776dbe732cbf7e63cc6079", 20, "八成"),        # STRIP
    ("68776dbf732cbf7e63cc611a",  1, "招"),          # STRIP
    ("68776dbf732cbf7e63cc611a",  9, "扩大"),        # EXPAND
    ("68776dbf732cbf7e63cc611a", 20, "受伤"),        # EXPAND
    ("68776dc0732cbf7e63cc6205", 34, "悲伤"),        # EXPAND
    ("69e72dea406bc428dc8fb259",  0, "锯子"),        # EXPAND
]

# (word_id, final_chinese)
WORD_FIXES: list[tuple[str, str]] = [
    ("69ae2de2d9a7a0223a2339df", "夏天"),            # EXPAND
    ("69e7296e9b0b96ebfb246ac7", "锯子"),            # EXPAND
]


def fix_words(apply: bool) -> int:
    coll = db.words_col()
    ops: list[UpdateOne] = []
    for wid, target in WORD_FIXES:
        doc = coll.find_one({"_id": ObjectId(wid)}, {"chinese": 1})
        if not doc:
            print(f"  [miss] words[{wid}] not found")
            continue
        cur = doc.get("chinese") or ""
        if cur == target:
            print(f"  [ok  ] words[{wid}] already '{target}'")
            continue
        print(f"  [fix ] words[{wid}]  {cur!r} → {target!r}")
        ops.append(UpdateOne({"_id": ObjectId(wid)}, {"$set": {"chinese": target}}))
    if apply and ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"  → wrote {res.modified_count}")
    return len(ops)


def fix_lessons(apply: bool) -> int:
    coll = db.lessons_col()
    # Group fixes by lesson _id to minimize write count
    per_lesson: dict[str, dict[str, str]] = {}
    for lid, idx, target in LESSON_FIXES:
        per_lesson.setdefault(lid, {})[f"newWords.{idx}.chinese"] = target

    ops: list[UpdateOne] = []
    for lid, setmap in per_lesson.items():
        lesson = coll.find_one({"_id": ObjectId(lid)}, {"newWords.chinese": 1})
        if not lesson:
            print(f"  [miss] lesson[{lid}] not found")
            continue
        nw = lesson.get("newWords") or []
        effective: dict[str, str] = {}
        for field_path, target in setmap.items():
            idx = int(field_path.split(".")[1])
            cur = nw[idx].get("chinese") if idx < len(nw) else None
            if cur == target:
                print(f"  [ok  ] lesson[{lid}].newWords[{idx}] already '{target}'")
                continue
            print(f"  [fix ] lesson[{lid}].newWords[{idx}]  {cur!r} → {target!r}")
            effective[field_path] = target
        if effective:
            ops.append(UpdateOne({"_id": ObjectId(lid)}, {"$set": effective}))
    if apply and ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"  → wrote {res.modified_count}")
    return len(ops)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write corrections to DB (default: dry-run)")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] correcting over-stripped paren entries")
    print(f"  DB: {db.DB_NAME}\n")

    print("== words ==")
    w = fix_words(args.apply)
    print(f"  {w} doc(s) need updating\n")

    print("== newlessons ==")
    l = fix_lessons(args.apply)
    print(f"  {l} doc(s) need updating\n")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
