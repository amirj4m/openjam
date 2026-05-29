"""
Extend the openjam core dataset with words pulled in by the books layer.

For every word that appears in any books/<slug>/words.json but NOT yet
in data/json/words_en.json, this script:

  1. Looks the word up in WordNet to harvest senses, parts of speech,
     definitions, and example sentences (same logic as
     scripts/generate_seed.py).
  2. Calls the Claude API for Persian translations + 1-3 category
     slugs (same prompt as the main pipeline).
  3. Appends the new word + senses to data/json/words_en.json,
     translations to data/json/translations_fa.json, and category
     assignments to data/json/word_categories.json.
  4. Rewrites the matching .sql files at data/sql/.

The added words use `source_list = 'book'` and a synthetic high
frequency_rank so they don't pretend to be among the top-19K frequent
words. After running this, run scripts/generate_audio.py and
scripts/generate_ipa_and_forms.py to fill audio + IPA + word_forms.

Idempotent: re-running skips any word already present.

Environment:
  ANTHROPIC_API_KEY  required
  OPENJAM_MODEL      optional, default claude-haiku-4-5-20251001
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = UUID("8f9c1b8a-0000-0000-0000-6f70656e6a6d")

# Synthetic rank offset for book-only words. Slots them after the
# 19282 frequency-ranked content words from wordfreq.
RANK_OFFSET = 100_000


POS_MAP = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}

CATEGORIES = [
    "food", "drink", "body", "clothing", "home", "animal", "plant", "nature",
    "vehicle", "tool", "family", "person", "profession", "relationship",
    "emotion", "society", "place", "country", "city", "travel", "work",
    "school", "sport", "arts", "religion", "media", "health", "science",
    "technology", "time", "money", "quality", "action", "abstract", "event",
]
CHECKPOINT_EVERY = 25


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value


def deterministic_uuid(text: str) -> str:
    return str(uuid5(NAMESPACE, text))


def cefr_for_rank(rank: int) -> str:
    if rank <= 500:
        return "A1"
    if rank <= 1500:
        return "A2"
    if rank <= 3000:
        return "B1"
    if rank <= 5000:
        return "B2"
    if rank <= 7000:
        return "C1"
    return "C2"


def extract_json(text: str) -> str:
    import re
    text = text.strip()
    if text.startswith("```"):
        body = text.split("```", 2)
        if len(body) >= 2:
            inner = body[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner.strip()
            if text.endswith("```"):
                text = text[:-3].strip()
    first = min((p for p in (text.find("{"), text.find("[")) if p != -1), default=-1)
    if first > 0:
        text = text[first:]
    last = max(text.rfind("}"), text.rfind("]"))
    if last != -1 and last < len(text) - 1:
        text = text[: last + 1]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return text


def collect_word_senses(word: str) -> list[dict] | None:
    """Look word up in WordNet, return cleaned senses or None if not found."""
    from nltk.corpus import wordnet as wn

    synsets = wn.synsets(word)
    if not synsets:
        return None

    senses_by_pos: dict[str, list] = {}
    for syn in synsets:
        pos = POS_MAP.get(syn.pos())
        if not pos:
            continue
        senses_by_pos.setdefault(pos, []).append(syn)

    senses: list[dict] = []
    for pos, syns in senses_by_pos.items():
        # Cap at 2 senses per POS to match the main pipeline's compactness.
        for order, syn in enumerate(syns[:2], 1):
            examples = syn.examples()
            senses.append({
                "id": deterministic_uuid(f"sense:{word}:{pos}:{order}"),
                "part_of_speech": pos,
                "sense_order": order,
                "definition_en": syn.definition(),
                "example_en": examples[0] if examples else None,
            })
    return senses if senses else None


def translate_word(client, model: str, word: str, senses: list[dict]) -> tuple[list[dict], list[str]]:
    sense_lines = "\n".join(
        f"{i + 1}. ({s['part_of_speech']}) {s['definition_en']}"
        for i, s in enumerate(senses)
    )
    cats_str = ", ".join(CATEGORIES)
    prompt = f"""You translate English vocabulary to Persian (Farsi, fa-IR) for learners.

English word: "{word}"

Senses:
{sense_lines}

For each sense in the SAME ORDER, provide:
- "meaning": Persian translation (word or short phrase, NO English, NO explanation)
- "example": a short, natural Persian sentence that uses the meaning in context

Also classify the WORD as a whole into 1-3 categories from this fixed list
(choose only the most relevant; pick exact slug strings):
{cats_str}

Return ONLY this JSON object, no Markdown, no prose:
{{"senses": [{{"meaning": "...", "example": "..."}}, ...], "categories": ["slug", ...]}}
"""
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = json.loads(extract_json(msg.content[0].text))
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    senses_out = parsed.get("senses", [])
    valid = set(CATEGORIES)
    cats_out = [c for c in parsed.get("categories", []) if c in valid]
    return senses_out, cats_out


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def sql_literal(s) -> str:
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


def write_words_sql(path: Path, words: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Openjam: English words + senses\n")
        f.write("-- Source: wordfreq (frequency) + Princeton WordNet 3.1 (senses)\n")
        f.write("-- Auto-generated by scripts/generate_seed.py + scripts/books/extend_openjam.py\n\n")
        f.write("BEGIN;\n\n")
        f.write("INSERT INTO words (id, english, frequency_rank, level, source_list) VALUES\n")
        rows = [
            "  ('{id}', {english}, {rank}, {level}, {source})".format(
                id=w["id"],
                english=sql_literal(w["english"]),
                rank=w["frequency_rank"],
                level=sql_literal(w["level"]),
                source=sql_literal(w["source_list"]),
            )
            for w in words
        ]
        f.write(",\n".join(rows))
        f.write("\nON CONFLICT (english) DO NOTHING;\n\n")
        f.write("INSERT INTO word_senses (id, word_id, part_of_speech, sense_order, definition_en, example_en) VALUES\n")
        sense_rows = []
        for w in words:
            for s in w["senses"]:
                sense_rows.append(
                    "  ('{id}', '{word_id}', {pos}, {order}, {definition}, {example})".format(
                        id=s["id"],
                        word_id=w["id"],
                        pos=sql_literal(s["part_of_speech"]),
                        order=s["sense_order"],
                        definition=sql_literal(s["definition_en"]),
                        example=sql_literal(s["example_en"]),
                    )
                )
        f.write(",\n".join(sense_rows))
        f.write("\nON CONFLICT (word_id, part_of_speech, sense_order) DO NOTHING;\n\n")
        f.write("COMMIT;\n")


def write_translations_sql(path: Path, translations: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Openjam: Persian (fa) translations of word senses\n\n")
        f.write("BEGIN;\n\n")
        f.write("INSERT INTO sense_translations (id, sense_id, language_code, meaning, example) VALUES\n")
        rows = [
            "  ('{id}', '{sense_id}', {lang}, {meaning}, {example})".format(
                id=t["id"],
                sense_id=t["sense_id"],
                lang=sql_literal(t["language_code"]),
                meaning=sql_literal(t["meaning"]),
                example=sql_literal(t.get("example")),
            )
            for t in translations
        ]
        f.write(",\n".join(rows))
        f.write("\nON CONFLICT (sense_id, language_code) DO NOTHING;\n\n")
        f.write("COMMIT;\n")


def write_word_categories_sql(path: Path, word_categories: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Openjam: word <-> category assignments.\n\n")
        if not word_categories:
            f.write("-- (no assignments)\n")
            return
        f.write("BEGIN;\n\n")
        f.write("INSERT INTO word_categories (word_id, category_id) VALUES\n")
        rows = [
            f"  ('{wc['word_id']}', '{wc['category_id']}')" for wc in word_categories
        ]
        f.write(",\n".join(rows))
        f.write("\nON CONFLICT (word_id, category_id) DO NOTHING;\n\n")
        f.write("COMMIT;\n")


def main() -> int:
    load_env_file()
    model = os.environ.get("OPENJAM_MODEL", "claude-haiku-4-5-20251001")

    # Load existing openjam core data.
    words_path = ROOT / "data/json/words_en.json"
    translations_path = ROOT / "data/json/translations_fa.json"
    word_categories_path = ROOT / "data/json/word_categories.json"

    words: list[dict] = json.loads(words_path.read_text(encoding="utf-8"))
    translations: list[dict] = json.loads(translations_path.read_text(encoding="utf-8"))
    word_categories: list[dict] = json.loads(word_categories_path.read_text(encoding="utf-8"))

    existing_english = {w["english"] for w in words}
    print(f"[1/4] openjam main has {len(words)} words")

    # Gather every word that appears in any book.
    books_dir = ROOT / "books"
    book_words: dict[str, int] = {}  # english -> first book's order (for stable rank)
    book_order_counter = 0
    for book_dir in sorted(books_dir.iterdir()):
        if not book_dir.is_dir():
            continue
        words_file = book_dir / "words.json"
        if not words_file.exists():
            continue
        payload = json.loads(words_file.read_text(encoding="utf-8"))
        for w in payload.get("words", []):
            eng = w["english"]
            if eng not in book_words:
                book_words[eng] = book_order_counter
                book_order_counter += 1

    pending = [w for w in book_words if w not in existing_english]
    print(f"[2/4] {len(book_words)} unique book words; {len(pending)} new -> add to openjam")

    if not pending:
        print("Nothing to do.")
        return 0

    # NLTK + Claude setup.
    print(f"[3/4] Adding {len(pending)} words via WordNet + {model}")
    import anthropic
    client = anthropic.Anthropic()

    new_words: list[dict] = []
    new_translations: list[dict] = []
    new_word_categories: list[dict] = []
    failures: list[tuple[str, str]] = []
    no_senses: list[str] = []
    start = time.time()

    for idx, english in enumerate(pending, 1):
        senses = collect_word_senses(english)
        if not senses:
            no_senses.append(english)
            continue

        word_id = deterministic_uuid(f"word:{english}")
        rank = RANK_OFFSET + book_words[english]

        try:
            sense_results, cat_slugs = translate_word(client, model, english, senses)
        except Exception as e:
            failures.append((english, str(e)))
            continue

        new_words.append({
            "id": word_id,
            "english": english,
            "frequency_rank": rank,
            "level": cefr_for_rank(rank),
            "source_list": "book",
            "senses": senses,
        })

        for sense, res in zip(senses, sense_results):
            if not isinstance(res, dict) or "meaning" not in res:
                continue
            new_translations.append({
                "id": deterministic_uuid(f"trans:{sense['id']}:fa"),
                "sense_id": sense["id"],
                "language_code": "fa",
                "meaning": str(res["meaning"]).strip(),
                "example": (str(res["example"]).strip() if res.get("example") else None),
            })

        for slug in cat_slugs:
            new_word_categories.append({
                "word_id": word_id,
                "category_id": deterministic_uuid(f"cat:{slug}"),
            })

        if idx % CHECKPOINT_EVERY == 0 or idx == len(pending):
            # Checkpoint: write merged data so a crash doesn't lose progress.
            merged_words = words + new_words
            merged_trans = translations + new_translations
            merged_wcats = word_categories + new_word_categories
            write_json(words_path, merged_words)
            write_json(translations_path, merged_trans)
            write_json(word_categories_path, merged_wcats)
            elapsed = time.time() - start
            eta = (elapsed / idx) * (len(pending) - idx) if idx else 0
            print(
                f"      [{idx}/{len(pending)}] +{len(new_words)} words "
                f"{english:<20s} elapsed={elapsed:5.0f}s eta={eta:5.0f}s",
                flush=True,
            )

    print(f"[4/4] Writing SQL")
    merged_words = words + new_words
    merged_trans = translations + new_translations
    merged_wcats = word_categories + new_word_categories
    write_words_sql(ROOT / "data/sql/words_en.sql", merged_words)
    write_translations_sql(ROOT / "data/sql/translations_fa.sql", merged_trans)
    write_word_categories_sql(ROOT / "data/sql/word_categories.sql", merged_wcats)
    print("      -> rewrote data/sql/words_en.sql, translations_fa.sql, word_categories.sql")

    print()
    print(f"DONE: added {len(new_words)} new words ({len(no_senses)} skipped — no WordNet)")
    if no_senses:
        print(f"  Skipped (no WordNet entry): {no_senses[:15]}{' ...' if len(no_senses) > 15 else ''}")
    if failures:
        print(f"FAILURES: {len(failures)}")
        for w, err in failures[:10]:
            print(f"  - {w}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
