"""
Scan all newlessons documents, find every word not resolved in the words
collection, insert missing entries, then print the real per-level word counts.

Run dry-run first (no --apply):
    python fix_missing_words.py

Then apply:
    python fix_missing_words.py --apply
"""

import argparse
import re
import sys
from collections import defaultdict

from bson import ObjectId
from pymongo import MongoClient
import db

# Override client with longer timeout for this script
_client = MongoClient(db.MONGODB_URI, serverSelectionTimeoutMS=30000)
_DB = _client[db.DB_NAME]

def _lessons_col():
    return _DB["newlessons"]

def _words_col():
    return _DB["words"]

_PAREN_RE = re.compile(r"[（(][^)）]*[)）]")


def _strip(s: str) -> str:
    return _PAREN_RE.sub("", (s or "").strip()).strip()


def _norm(s: str) -> str:
    return (s or "").strip()


def _resolve(ch: str, py: str, en: str, by_triple: dict, by_chinese: dict) -> dict | None:
    match = by_triple.get((_norm(ch), _norm(py), _norm(en)))
    if not match:
        cands = by_chinese.get(_norm(ch), [])
        if len(cands) == 1:
            match = cands[0]
    if not match:
        stripped = _strip(ch)
        if stripped and stripped != _norm(ch):
            cands = by_chinese.get(stripped, [])
            if len(cands) == 1:
                match = cands[0]
    if not match:
        # Pinyin-narrowed: multiple chinese entries, narrow by pinyin
        for base_ch in (_norm(ch), _strip(ch)):
            cands_py = [c for c in by_chinese.get(base_ch, []) if c.get("pinyin") == _norm(py)]
            if len(cands_py) == 1:
                match = cands_py[0]
                break
    return match


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually insert missing words")
    args = ap.parse_args()

    lessons_col = _lessons_col()
    words_col = _words_col()

    # ── Build per-level index of all words in words collection ────────────
    print("Loading words collection …")
    words_by_level: dict[int, dict] = {}   # level → {by_triple, by_chinese}
    for wd in words_col.find({}, {"chinese": 1, "pinyin": 1, "english": 1, "level": 1}):
        lv = wd.get("level")
        if lv not in words_by_level:
            words_by_level[lv] = {"by_triple": {}, "by_chinese": defaultdict(list)}
        ch = _norm(wd.get("chinese", ""))
        py = _norm(wd.get("pinyin", ""))
        en = _norm(wd.get("english", ""))
        entry = {"_id": wd["_id"], "chinese": ch, "pinyin": py, "english": en}
        words_by_level[lv]["by_triple"][(ch, py, en)] = entry
        words_by_level[lv]["by_chinese"][ch].append(entry)

    # ── Scan newlessons, collect missing ──────────────────────────────────
    print("Scanning newlessons …\n")

    # Track what we plan to insert: keyed by (chinese, level) to avoid dupes
    to_insert: dict[tuple[str, int], dict] = {}
    ambiguous: list[tuple] = []   # (level, topic, chinese) where 2+ words exist
    missing_count_by_level: dict[str, int] = defaultdict(int)

    for lesson in lessons_col.find({}, {
        "hskLevel": 1, "topicTitle": 1, "topicIndex": 1,
        "newWords.chinese": 1, "newWords.pinyin": 1, "newWords.english": 1,
    }).sort([("hskLevel", 1), ("topicIndex", 1)]):
        level_str = str(lesson.get("hskLevel", ""))
        try:
            level_int = int(level_str)
        except ValueError:
            continue
        topic = lesson.get("topicTitle", "")

        idx = words_by_level.get(level_int, {"by_triple": {}, "by_chinese": defaultdict(list)})
        bt = idx["by_triple"]
        bc = idx["by_chinese"]

        for w in lesson.get("newWords") or []:
            ch = _norm(w.get("chinese", ""))
            py = _norm(w.get("pinyin", ""))
            en = _norm(w.get("english", ""))
            if not ch:
                continue

            match = _resolve(ch, py, en, bt, bc)
            if match:
                continue  # already in words collection

            # Distinguish: zero candidates vs multiple (ambiguous)
            raw_cands = bc.get(ch, []) or bc.get(_strip(ch), [])
            if len(raw_cands) > 1:
                ambiguous.append((level_int, topic, ch, py, en))
                continue

            key = (_strip(ch) or ch, level_int)
            if key not in to_insert:
                to_insert[key] = {
                    "chinese": _strip(ch) or ch,
                    "pinyin": py,
                    "english": en,
                    "level": level_int,
                }
                missing_count_by_level[level_str] += 1

    # ── Report ────────────────────────────────────────────────────────────
    if not to_insert and not ambiguous:
        print("✓ No missing words found — words collection is complete.")
    else:
        if to_insert:
            print(f"Missing words to insert ({len(to_insert)} total):")
            by_lv: dict[int, list] = defaultdict(list)
            for entry in to_insert.values():
                by_lv[entry["level"]].append(entry)
            for lv in sorted(by_lv):
                print(f"\n  HSK {lv} ({len(by_lv[lv])} missing):")
                for e in sorted(by_lv[lv], key=lambda x: x["chinese"]):
                    print(f"    {e['chinese']:12s}  {e['pinyin']:20s}  {e['english'][:50]}")

        if ambiguous:
            print(f"\nAmbiguous (2+ words entries for same chinese — skipped):")
            for lv, topic, ch, py, en in sorted(ambiguous):
                print(f"  HSK{lv} [{topic}] {ch!r} py={py!r}")

    # ── Insert ────────────────────────────────────────────────────────────
    if to_insert:
        if not args.apply:
            print(f"\n(dry-run) re-run with --apply to insert {len(to_insert)} words.")
        else:
            docs = list(to_insert.values())
            result = words_col.insert_many(docs)
            print(f"\nInserted {len(result.inserted_ids)} documents.")

            # Update the in-memory index so the count below is accurate
            for doc, oid in zip(docs, result.inserted_ids):
                lv = doc["level"]
                if lv not in words_by_level:
                    words_by_level[lv] = {"by_triple": {}, "by_chinese": defaultdict(list)}
                entry = {"_id": oid, **doc}
                words_by_level[lv]["by_triple"][(doc["chinese"], doc["pinyin"], doc["english"])] = entry
                words_by_level[lv]["by_chinese"][doc["chinese"]].append(entry)

    # ── Count per-level totals in words collection ────────────────────────
    print("\n─── Word counts in `words` collection per level ───")
    pipeline = [{"$group": {"_id": "$level", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    counts = {str(r["_id"]): r["count"] for r in words_col.aggregate(pipeline)}
    for lv in ["1", "2", "3", "4", "5", "6"]:
        n = counts.get(lv, 0)
        print(f"  HSK {lv}: {n}")

    print("\nTOTAL_WORDS_PER_LEVEL = {")
    for lv in ["1", "2", "3", "4", "5", "6"]:
        print(f"  {lv}: {counts.get(lv, 0)},")
    print("};")

    return 0


if __name__ == "__main__":
    sys.exit(main())
