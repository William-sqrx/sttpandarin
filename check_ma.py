"""Look up 吗 in words + newlessons to find its current english string."""

import db


def main() -> None:
    words = db.words_col()
    lessons = db.lessons_col()

    print("── words collection ─────────────────────────────────────────")
    for w in words.find({"chinese": "吗"}, {"_id": 1, "level": 1, "pinyin": 1, "english": 1}):
        print(f"  level={w.get('level')}  pinyin={w.get('pinyin')!r}  english={w.get('english')!r}")

    print("\n── newlessons.newWords entries ──────────────────────────────")
    for L in lessons.find({"newWords.chinese": "吗"}, {"hskLevel": 1, "topicIndex": 1, "newWords": 1}):
        for nw in L.get("newWords", []):
            if nw.get("chinese") == "吗":
                print(
                    f"  hsk={L.get('hskLevel')} part={L.get('topicIndex')}  "
                    f"pinyin={nw.get('pinyin')!r}  english={nw.get('english')!r}"
                )


if __name__ == "__main__":
    main()
