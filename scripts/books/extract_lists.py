"""
Read source word lists from upstream public repos, extract just the
English head words, and write them to books/<slug>/meta.json +
words.json in our standard format.

Source files are expected at:
  <SOURCE_DIR>/504.json           lessons + words schema
  <SOURCE_DIR>/awl.json           AWL Coxhead, 570 entries (en/tr/ex)
  <SOURCE_DIR>/toefl-jihunjo.json TOEFL words with level field
  <SOURCE_DIR>/gre3000.json       GRE3000 csv-like rows

Run once after downloading the four sources. The output is committed
to git; the source files are NOT (they live in a tmp dir).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(os.environ.get("SOURCE_DIR", r"C:\Users\amirj\openjam_tmp_books"))
BOOKS = ROOT / "books"


def write_book(
    slug: str,
    meta: dict,
    words_in_order: list[dict],
) -> None:
    book_dir = BOOKS / slug
    book_dir.mkdir(parents=True, exist_ok=True)

    # Deduplicate by English form (lower-case), keeping the first occurrence.
    seen: set[str] = set()
    cleaned: list[dict] = []
    for w in words_in_order:
        eng = w["english"].lower().strip()
        if not eng or eng in seen:
            continue
        seen.add(eng)
        cleaned.append({**w, "english": eng})

    meta = {**meta, "word_count": len(cleaned), "slug": slug}
    (book_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (book_dir / "words.json").write_text(
        json.dumps({"version": "1.0", "words": cleaned}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[{slug}] wrote {len(cleaned)} words")


def build_504() -> None:
    raw = json.loads((SRC / "504.json").read_text(encoding="utf-8"))
    words: list[dict] = []
    for lesson_entry in raw:
        lesson = lesson_entry.get("lesson")
        for i, w in enumerate(lesson_entry.get("words", []), 1):
            words.append({
                "english": w["word"],
                "group": f"lesson-{lesson}",
                "order": i,
            })
    write_book(
        "504-essential",
        {
            "name_en": "504 Essential English Words",
            "name_fa": "۵۰۴ لغت ضروری",
            "description": "Classic foundational English vocabulary list, popular in Persian-Iranian study tradition. 42 lessons of 12 words each.",
            "source_attribution": "Word list compiled from public study materials (shahsuvarli/504-essential-words). Definitions and Persian translations in this dataset are original, generated via Claude.",
            "level": "intermediate",
            "has_groups": True,
            "group_label": "Lesson",
            "group_label_fa": "درس",
        },
        words,
    )


def build_ielts_awl() -> None:
    raw = json.loads((SRC / "awl.json").read_text(encoding="utf-8"))
    # The source has a "sub" field (sublist 1-10 by frequency).
    words: list[dict] = []
    for i, w in enumerate(raw, 1):
        words.append({
            "english": w["en"],
            "group": f"sublist-{w.get('sub', 1)}",
            "order": i,
        })
    write_book(
        "ielts",
        {
            "name_en": "IELTS Academic Word List",
            "name_fa": "لغات آکادمیک آیلتس",
            "description": "Averil Coxhead's Academic Word List (AWL): 570 head words organized into 10 sublists by frequency in academic texts. Core IELTS prep vocabulary.",
            "source_attribution": "Academic Word List by Averil Coxhead (publicly available research output). Word list compiled from aytanozu/academic-word-list-en-tr. Definitions and Persian translations original.",
            "level": "upper-intermediate",
            "has_groups": True,
            "group_label": "Sublist",
            "group_label_fa": "زیرلیست",
        },
        words,
    )


def build_toefl() -> None:
    raw = json.loads((SRC / "toefl-jihunjo.json").read_text(encoding="utf-8"))
    # Filter to the 0-60 and 60-80 level words (most relevant for TOEFL prep).
    # The full set has 3319 entries spanning advanced GRE-ish vocab too.
    LEVELS_TO_KEEP = {"0-60", "60-80", "80-100"}
    words: list[dict] = []
    for w in raw:
        if w.get("level") not in LEVELS_TO_KEEP:
            continue
        words.append({
            "english": w["word"],
            "group": f"level-{w['level']}",
            "order": w.get("id", 0),
        })
    # Sort by id within each level
    words.sort(key=lambda x: (x["group"], x["order"]))
    write_book(
        "toefl",
        {
            "name_en": "TOEFL Essential Vocabulary",
            "name_fa": "لغات ضروری تافل",
            "description": "Common TOEFL vocabulary across iBT score bands (0-60, 60-80, 80-100). Curated for test-taker progression.",
            "source_attribution": "Word list compiled from JIHUNJO123/toefl-prep-vocabulary public repository. Definitions and Persian translations original.",
            "level": "upper-intermediate",
            "has_groups": True,
            "group_label": "Score band",
            "group_label_fa": "محدوده نمره",
        },
        words,
    )


def build_gre() -> None:
    raw = json.loads((SRC / "gre3000.json").read_text(encoding="utf-8"))
    # raw["data"][0] is the header row; skip it.
    rows = raw["data"][1:]
    words: list[dict] = []
    for i, row in enumerate(rows, 1):
        eng = row[0].strip()
        if not eng:
            continue
        words.append({
            "english": eng,
            "order": i,
        })
    write_book(
        "gre",
        {
            "name_en": "GRE 3000 Vocabulary",
            "name_fa": "لغات GRE ۳۰۰۰",
            "description": "Comprehensive GRE high-frequency vocabulary list. 3000+ advanced English words drawn from GRE test corpora.",
            "source_attribution": "Word list compiled from Dramalf/GRE3000-cli public repository. Definitions and Persian translations original.",
            "level": "advanced",
            "has_groups": False,
        },
        words,
    )


def main() -> int:
    build_504()
    build_ielts_awl()
    build_toefl()
    build_gre()
    return 0


if __name__ == "__main__":
    sys.exit(main())
