"""MongoDB access for the HSK Browser tab.

Source of truth for word entries (audio + meaning) is the flat `words` collection.
The `newlessons` collection is used only to group words into lessons (parts) for
browsing.

Collections:
- newlessons: { hskLevel: "1".."6", topicTitle, topicIndex, newWords: [{chinese, pinyin, english, ...}] }
- words:      { _id, chinese, pinyin, english, level: Number, audioBlob }

Lookup strategy: each newWords entry is matched against `words` by
chinese + pinyin + english. The matched word's `_id` is the identity used by
audio / regenerate / edit endpoints.
"""

from functools import lru_cache
from typing import Any

from bson import Binary, ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection

# Let pymongo/dnspython use the platform's default resolver.


MONGODB_URI = "mongodb+srv://williamjacob0910:william0910@chinesefish0910.kjyudat.mongodb.net/?appName=ChineseFish0910"
DB_NAME = "HelloGuru"


class DBError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    return MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)


def lessons_col() -> Collection:
    return _client()[DB_NAME]["newlessons"]


def words_col() -> Collection:
    return _client()[DB_NAME]["words"]


def fish_anims_col() -> Collection:
    """Sprite-sheet docs: { name, idx, sheet (Binary PNG), frames, frameW,
    frameH, created_at }. Compound unique index (name, idx) lets us upsert
    by species + sheet number — re-running the batch is safely idempotent.
    """
    col = _client()[DB_NAME]["fish_anims"]
    # ensure_index is a no-op after the first call.
    col.create_index([("name", 1), ("idx", 1)], unique=True, name="name_idx_unique")
    return col


def fish_anims_skips_col() -> Collection:
    """Persistent skip list: { name, skipped_at }. The batch worker queries
    this on startup so a "skip" decision survives dyno restarts and the
    next time the user clicks Start."""
    col = _client()[DB_NAME]["fish_anims_skips"]
    col.create_index("name", unique=True, name="name_unique")
    return col


def _norm(s: str) -> str:
    return (s or "").strip()


_PAREN_RE = None


def _strip_parens(s: str) -> str:
    """Strip any (..) / （..) parenthetical content from a chinese string.
    textbook entries like '锯(子)' or '没有（没)' map to plain-form words
    stored as '锯' / '没有' in the words collection.
    """
    import re
    global _PAREN_RE
    if _PAREN_RE is None:
        _PAREN_RE = re.compile(r"[（(][^)）]*[)）]")
    return _PAREN_RE.sub("", s or "").strip()


def _to_bytes(blob: Any) -> bytes | None:
    if blob is None:
        return None
    if isinstance(blob, Binary):
        return bytes(blob)
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    # mongoose sometimes stores Buffer as {buffer: <Binary>} or {data: [..]}
    if isinstance(blob, dict):
        inner = blob.get("buffer") or blob.get("data")
        if isinstance(inner, (bytes, bytearray, Binary)):
            return bytes(inner)
    return None


def list_levels() -> list[str]:
    return ["1", "2", "3", "4", "5", "6"]


def list_lessons(level: str) -> list[dict[str, Any]]:
    cur = lessons_col().find(
        {"hskLevel": level},
        {"topicTitle": 1, "topicIndex": 1, "englishTitle": 1, "newWords.chinese": 1},
    ).sort("topicIndex", 1)
    out = []
    for doc in cur:
        out.append({
            "_id": str(doc["_id"]),
            "topicTitle": doc.get("topicTitle", ""),
            "topicIndex": doc.get("topicIndex"),
            "englishTitle": doc.get("englishTitle", ""),
            "wordCount": len(doc.get("newWords") or []),
        })
    return out


def get_lesson_words(lesson_id: str) -> dict[str, Any]:
    """Return the lesson's word list, with each word resolved to its
    canonical entry in the `words` collection (id, current pinyin/english,
    audio presence). Words missing from `words` are still included with
    `wordId = None`.
    """
    lesson = lessons_col().find_one(
        {"_id": ObjectId(lesson_id)},
        {"topicTitle": 1, "hskLevel": 1, "newWords.chinese": 1, "newWords.pinyin": 1, "newWords.english": 1},
    )
    if not lesson:
        raise DBError("lesson not found")

    level_int: int | None = None
    try:
        level_int = int(str(lesson.get("hskLevel", "")).strip())
    except ValueError:
        level_int = None

    new_words = lesson.get("newWords") or []

    # Bulk-fetch matching words for this level, then index them so we don't
    # do N queries.
    triples = [
        (_norm(w.get("chinese", "")), _norm(w.get("pinyin", "")), _norm(w.get("english", "")))
        for w in new_words
    ]
    by_triple: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_chinese: dict[str, list[dict[str, Any]]] = {}

    # Include both raw chinese and paren-stripped variants in the lookup set
    # so entries like '没有（没)' still resolve to '没有' in words.
    chinese_set: set[str] = set()
    for t in triples:
        if t[0]:
            chinese_set.add(t[0])
            stripped = _strip_parens(t[0])
            if stripped and stripped != t[0]:
                chinese_set.add(stripped)
    if chinese_set:
        wfilter: dict[str, Any] = {"chinese": {"$in": list(chinese_set)}}
        if level_int is not None:
            wfilter["level"] = level_int
        for wd in words_col().find(
            wfilter,
            {"chinese": 1, "pinyin": 1, "english": 1, "level": 1, "audioBlob": 1},
        ):
            ch = _norm(wd.get("chinese", ""))
            py = _norm(wd.get("pinyin", ""))
            en = _norm(wd.get("english", ""))
            entry = {
                "wordId": str(wd["_id"]),
                "chinese": ch,
                "pinyin": py,
                "english": en,
                "level": wd.get("level"),
                "hasAudio": bool(_to_bytes(wd.get("audioBlob"))),
            }
            by_triple[(ch, py, en)] = entry
            by_chinese.setdefault(ch, []).append(entry)

    out_words = []
    for i, (ch, py, en) in enumerate(triples):
        match = by_triple.get((ch, py, en))
        if not match:
            # Fall back to chinese-only match (pinyin/english may have drifted).
            candidates = by_chinese.get(ch, [])
            if len(candidates) == 1:
                match = candidates[0]
        if not match:
            # Try paren-stripped chinese: '没有（没)' → '没有', '锯(子)' → '锯'.
            stripped = _strip_parens(ch)
            if stripped and stripped != ch:
                candidates = by_chinese.get(stripped, [])
                if len(candidates) == 1:
                    match = candidates[0]
        if not match:
            # Pinyin-narrowed fallback: multiple chinese entries but only one
            # matches the given pinyin (e.g. 系 jì vs 系 xì).
            for base_ch in (ch, _strip_parens(ch)):
                cands_py = [c for c in by_chinese.get(base_ch, []) if c.get("pinyin") == py]
                if len(cands_py) == 1:
                    match = cands_py[0]
                    break

        if match:
            out_words.append({
                "index": i,
                "wordId": match["wordId"],
                "chinese": match["chinese"],
                "pinyin": match["pinyin"],
                "english": match["english"],
                "hasAudio": match["hasAudio"],
            })
        else:
            out_words.append({
                "index": i,
                "wordId": None,
                "chinese": ch,
                "pinyin": py,
                "english": en,
                "hasAudio": False,
            })

    return {
        "_id": str(lesson["_id"]),
        "topicTitle": lesson.get("topicTitle", ""),
        "hskLevel": lesson.get("hskLevel", ""),
        "words": out_words,
    }


def get_word_audio(word_id: str) -> bytes | None:
    doc = words_col().find_one({"_id": ObjectId(word_id)}, {"audioBlob": 1})
    if not doc:
        return None
    return _to_bytes(doc.get("audioBlob"))


def get_word(word_id: str) -> dict[str, Any] | None:
    doc = words_col().find_one(
        {"_id": ObjectId(word_id)},
        {"chinese": 1, "pinyin": 1, "english": 1, "level": 1},
    )
    if not doc:
        return None
    return {
        "wordId": str(doc["_id"]),
        "chinese": _norm(doc.get("chinese", "")),
        "pinyin": _norm(doc.get("pinyin", "")),
        "english": _norm(doc.get("english", "")),
        "level": doc.get("level"),
    }


def update_word_audio(word_id: str, mp3_bytes: bytes) -> None:
    blob = Binary(mp3_bytes)
    words_col().update_one(
        {"_id": ObjectId(word_id)},
        {"$set": {"audioBlob": blob}},
    )


def update_word_fields(
    word_id: str,
    *,
    pinyin: str | None = None,
    english: str | None = None,
) -> dict[str, Any]:
    update: dict[str, Any] = {}
    if pinyin is not None:
        update["pinyin"] = pinyin
    if english is not None:
        update["english"] = english
    if update:
        res = words_col().update_one({"_id": ObjectId(word_id)}, {"$set": update})
        if res.matched_count == 0:
            raise DBError("word not found")
    word = get_word(word_id)
    if not word:
        raise DBError("word not found")
    return word
