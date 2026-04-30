"""Nuke the cached Quiz collection so the next /api/quiz/generate call
rebuilds questions from the live `words` collection.

    python clear_quiz_cache.py            # show how many docs would go
    python clear_quiz_cache.py --apply    # actually delete them
"""

import argparse
import sys

import db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    quiz_col = db._client()[db.DB_NAME]["quizzes"]
    n = quiz_col.count_documents({})
    print(f"  Quiz docs in DB: {n}")
    if not args.apply:
        print("\n(dry-run) re-run with --apply to drop them all.")
        return 0
    r = quiz_col.delete_many({})
    print(f"  deleted={r.deleted_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
