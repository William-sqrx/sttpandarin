"""MongoDB access for the HSK Browser tab.

Collections:
- newlessons: { hskLevel: "1".."6", topicTitle, topicIndex, newWords: [{chinese, pinyin, english, audioBlob, ...}] }
- words:      { chinese, pinyin, english, level: Number, audioBlob }

Regenerated audio is written to both: the lesson's embedded newWords[idx].audioBlob
and the matching flat words doc (matched on chinese+pinyin+english).
"""

import os
from functools import lru_cache
from typing import Any

from bson import Binary, ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection


DB_NAME = os.getenv("MONGODB_DB", "HelloGuru")


class DBError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise DBError("MONGODB_URI is not set")
    return MongoClient(uri, serverSelectionTimeoutMS=8000)


def lessons_col() -> Collection:
    return _client()[DB_NAME]["newlessons"]


def words_col() -> Collection:
    return _client()[DB_NAME]["words"]


def _norm(s: str) -> str:
    return (s or "").strip()


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
    doc = lessons_col().find_one(
        {"_id": ObjectId(lesson_id)},
        {"newWords.audioBlob": 0},
    )
    if not doc:
        raise DBError("lesson not found")
    words = []
    for i, w in enumerate(doc.get("newWords") or []):
        words.append({
            "index": i,
            "chinese": w.get("chinese", ""),
            "pinyin": w.get("pinyin", ""),
            "english": w.get("english", ""),
            "hasAudio": False,  # filled in below
        })
    # Compute hasAudio without loading blobs
    raw = lessons_col().find_one(
        {"_id": ObjectId(lesson_id)},
        {"newWords.audioBlob": 1},
    )
    for i, w in enumerate((raw or {}).get("newWords") or []):
        if i < len(words):
            words[i]["hasAudio"] = bool(w.get("audioBlob"))
    return {
        "_id": str(doc["_id"]),
        "topicTitle": doc.get("topicTitle", ""),
        "hskLevel": doc.get("hskLevel", ""),
        "words": words,
    }


def get_word_audio(lesson_id: str, index: int) -> bytes | None:
    doc = lessons_col().find_one(
        {"_id": ObjectId(lesson_id)},
        {"newWords": 1},
    )
    if not doc:
        return None
    words = doc.get("newWords") or []
    if index < 0 or index >= len(words):
        return None
    blob = words[index].get("audioBlob")
    if not blob:
        return None
    if isinstance(blob, Binary):
        return bytes(blob)
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    # mongoose sometimes stores Buffer as {buffer: <Binary>}
    if isinstance(blob, dict):
        inner = blob.get("buffer") or blob.get("data")
        if isinstance(inner, (bytes, bytearray, Binary)):
            return bytes(inner)
    return None


def _find_word_in_lesson(lesson_id: str, index: int) -> dict[str, Any] | None:
    doc = lessons_col().find_one(
        {"_id": ObjectId(lesson_id)},
        {"newWords.chinese": 1, "newWords.pinyin": 1, "newWords.english": 1, "hskLevel": 1},
    )
    if not doc:
        return None
    words = doc.get("newWords") or []
    if index < 0 or index >= len(words):
        return None
    w = words[index]
    return {
        "chinese": _norm(w.get("chinese", "")),
        "pinyin": _norm(w.get("pinyin", "")),
        "english": _norm(w.get("english", "")),
        "hskLevel": doc.get("hskLevel", ""),
    }


def update_word_audio(lesson_id: str, index: int, mp3_bytes: bytes) -> None:
    # Snapshot the word's identity BEFORE any pinyin/meaning edits, so the
    # words-collection match still works.
    before = _find_word_in_lesson(lesson_id, index)
    blob = Binary(mp3_bytes)
    lessons_col().update_one(
        {"_id": ObjectId(lesson_id)},
        {"$set": {f"newWords.{index}.audioBlob": blob}},
    )
    if before and before["chinese"] and before["pinyin"] and before["english"]:
        words_col().update_many(
            {
                "chinese": before["chinese"],
                "pinyin": before["pinyin"],
                "english": before["english"],
            },
            {"$set": {"audioBlob": blob}},
        )


def update_word_fields(
    lesson_id: str,
    index: int,
    *,
    pinyin: str | None = None,
    english: str | None = None,
) -> dict[str, Any]:
    before = _find_word_in_lesson(lesson_id, index)
    if not before:
        raise DBError("word not found")

    set_lesson: dict[str, Any] = {}
    words_filter_before = {
        "chinese": before["chinese"],
        "pinyin": before["pinyin"],
        "english": before["english"],
    }
    words_set: dict[str, Any] = {}

    if pinyin is not None and pinyin != before["pinyin"]:
        set_lesson[f"newWords.{index}.pinyin"] = pinyin
        words_set["pinyin"] = pinyin
    if english is not None and english != before["english"]:
        set_lesson[f"newWords.{index}.english"] = english
        words_set["english"] = english

    if set_lesson:
        lessons_col().update_one(
            {"_id": ObjectId(lesson_id)},
            {"$set": set_lesson},
        )
    if words_set and before["chinese"]:
        words_col().update_many(words_filter_before, {"$set": words_set})

    return _find_word_in_lesson(lesson_id, index) or {}
