"""
Openjam seed generator.

Pipeline:
  1. Pull top-N most-frequent English words from `wordfreq` (modern corpus).
  2. Look up each word in Princeton WordNet (via NLTK) to get senses,
     part of speech, definition, and example sentence.
  3. Ask the Claude API in one call per word for:
       - Persian (fa) translation + example per sense
       - 1-3 category slugs for the word from a fixed taxonomy
  4. Emit:
       data/json/words_en.json
       data/json/translations_fa.json
       data/json/categories.json
       data/json/word_categories.json
       data/sql/words_en.sql
       data/sql/translations_fa.sql
       data/sql/categories.sql
       data/sql/word_categories.sql

Resumable: existing translations and category assignments are loaded from
their JSON files; words already covered are skipped (no API call).

Idempotent: re-running produces the same UUIDs (uuid5 over a fixed namespace).

Environment:
  ANTHROPIC_API_KEY    required for the translation step
  N_WORDS              optional, default 1000
  OPENJAM_MODEL        optional, default claude-haiku-4-5-20251001
  SKIP_TRANSLATE=1     stop after writing words_en.{sql,json}

Usage:
  python scripts/generate_seed.py             # default 1000 words
  N_WORDS=5000 python scripts/generate_seed.py
  N_WORDS=10 python scripts/generate_seed.py  # quick smoke test
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid5

# Repository root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent

# Stable namespace so re-runs produce identical UUIDs.
NAMESPACE = UUID("8f9c1b8a-0000-0000-0000-6f70656e6a6d")  # "openjm" in trailing bytes

# WordNet POS letter -> Openjam part_of_speech enum
POS_MAP = {
    "n": "noun",
    "v": "verb",
    "a": "adjective",
    "s": "adjective",
    "r": "adverb",
}

CHECKPOINT_EVERY = 25  # write JSON every N processed words during translation

# Flat taxonomy of 35 categories used to tag words. Slug must match the
# schema regex ^[a-z0-9-]+$. Names are English display labels.
# Grouped only for readability — the schema is flat (no parent_id used here).
CATEGORIES: list[tuple[str, str]] = [
    # Physical / concrete
    ("food", "Food"),
    ("drink", "Drink"),
    ("body", "Body parts"),
    ("clothing", "Clothing"),
    ("home", "Home and household"),
    ("animal", "Animals"),
    ("plant", "Plants"),
    ("nature", "Nature and weather"),
    ("vehicle", "Vehicles and transport"),
    ("tool", "Tools and devices"),
    # People & society
    ("family", "Family"),
    ("person", "People"),
    ("profession", "Professions"),
    ("relationship", "Relationships"),
    ("emotion", "Emotions"),
    ("society", "Society and politics"),
    # Places
    ("place", "Places"),
    ("country", "Countries"),
    ("city", "Cities"),
    ("travel", "Travel"),
    # Activities & culture
    ("work", "Work and business"),
    ("school", "School and education"),
    ("sport", "Sports and games"),
    ("arts", "Arts and culture"),
    ("religion", "Religion"),
    ("media", "Media and communication"),
    # Sciences
    ("health", "Health and medicine"),
    ("science", "Science"),
    ("technology", "Technology"),
    # Abstract
    ("time", "Time"),
    ("money", "Money and finance"),
    ("quality", "Qualities and descriptions"),
    ("action", "Actions and movement"),
    ("abstract", "Abstract concepts"),
    ("event", "Events"),
]

CATEGORY_SLUGS = [slug for slug, _ in CATEGORIES]


def load_env_file() -> None:
    """Load KEY=VALUE pairs from .env at the repo root, if present.

    Keeps ANTHROPIC_API_KEY out of the shell environment and out of git
    (.env is gitignored). Skips lines that are blank or start with #.
    Won't override an env var already set by the user's shell.
    """
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
        # Overwrite empty values too — sometimes a parent shell exports
        # the key as "" which would otherwise hide the .env value.
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


def ensure_nltk_data() -> None:
    """Download WordNet + Brown corpus on first run."""
    import nltk

    resources = [
        ("wordnet", "corpora/wordnet"),
        ("omw-1.4", "corpora/omw-1.4"),
        ("brown", "corpora/brown"),
        ("universal_tagset", "taggers/universal_tagset"),
    ]
    for resource, path in resources:
        try:
            nltk.data.find(path)
        except LookupError:
            print(f"  Downloading NLTK resource: {resource}")
            nltk.download(resource, quiet=True)


class BrownFilter:
    """Filters wordfreq candidates using Brown corpus POS statistics.

    Why: WordNet is encyclopedic. Looking up 'be' returns the noun
    'beryllium' as a sense; 'as' returns 'arsenic'. These chemistry
    /geography entries pollute the output for vocab learning.

    Strategy: for each candidate word, check Brown corpus to find
    which POS the word is actually used as. Then only keep WordNet
    senses whose POS matches a significant Brown usage of the word.
    """

    CONTENT_POS = {"NOUN", "VERB", "ADJ", "ADV"}
    BROWN_TO_WN = {"NOUN": "n", "VERB": "v", "ADJ": "a", "ADV": "r"}
    # POS must account for at least this fraction of the word's Brown usage
    # to be considered acceptable. Filters out rare/spurious senses.
    SIGNIFICANT_FRACTION = 0.10

    def __init__(self):
        from collections import Counter, defaultdict

        from nltk.corpus import brown

        counts: dict[str, Counter] = defaultdict(Counter)
        for word, pos in brown.tagged_words(tagset="universal"):
            counts[word.lower()][pos] += 1
        self._counts = counts

    def primary_pos(self, word: str) -> str | None:
        c = self._counts.get(word)
        return c.most_common(1)[0][0] if c else None

    def is_content(self, word: str) -> bool:
        pos = self.primary_pos(word)
        return pos in self.CONTENT_POS

    def acceptable_wn_pos(self, word: str) -> set[str]:
        c = self._counts.get(word)
        if not c:
            return set()
        total = sum(c.values())
        return {
            self.BROWN_TO_WN[pos]
            for pos, n in c.items()
            if pos in self.BROWN_TO_WN and n / total >= self.SIGNIFICANT_FRACTION
        }


def collect_words(target_count: int) -> list[dict]:
    """Return up to `target_count` words enriched with WordNet senses."""
    from nltk.corpus import wordnet as wn
    from nltk.stem import WordNetLemmatizer
    from wordfreq import top_n_list

    brown_filter = BrownFilter()
    lemmatizer = WordNetLemmatizer()
    BROWN_TO_LEMMA_POS = {"NOUN": "n", "VERB": "v", "ADJ": "a", "ADV": "r"}

    # Over-fetch since many top-frequency words are functional and get dropped.
    candidates = top_n_list("en", max(target_count * 10, 10000))

    words: list[dict] = []
    seen_lemmas: set[str] = set()

    for rank, raw in enumerate(candidates, 1):
        if len(words) >= target_count:
            break

        surface = raw.lower().strip()
        if not surface.isalpha() or len(surface) < 2:
            continue

        if not brown_filter.is_content(surface):
            continue

        # Lemmatize using the word's primary POS in Brown so 'was' -> 'be',
        # 'people' stays 'people', 'children' -> 'child', etc.
        primary = brown_filter.primary_pos(surface)
        lemma_pos = BROWN_TO_LEMMA_POS.get(primary, "n")
        word = lemmatizer.lemmatize(surface, pos=lemma_pos)

        if word in seen_lemmas:
            continue
        seen_lemmas.add(word)

        # Re-check filters on the lemma (may differ from the surface form).
        if not brown_filter.is_content(word):
            continue

        allowed_pos = brown_filter.acceptable_wn_pos(word) or brown_filter.acceptable_wn_pos(surface)
        if not allowed_pos:
            continue

        synsets = wn.synsets(word)
        if not synsets:
            continue

        word_id = deterministic_uuid(f"word:{word}")
        senses_by_pos: dict[str, list] = {}
        for syn in synsets:
            syn_pos = syn.pos()
            # Drop senses whose POS is not how this word is actually used.
            wn_key = "a" if syn_pos == "s" else syn_pos
            if wn_key not in allowed_pos:
                continue
            pos = POS_MAP.get(syn_pos)
            if not pos:
                continue
            senses_by_pos.setdefault(pos, []).append(syn)

        if not senses_by_pos:
            continue

        senses = []
        for pos, syns in senses_by_pos.items():
            # Cap at 2 senses per POS to keep dataset compact for v0.1.
            for order, syn in enumerate(syns[:2], 1):
                examples = syn.examples()
                senses.append(
                    {
                        "id": deterministic_uuid(f"sense:{word}:{pos}:{order}"),
                        "part_of_speech": pos,
                        "sense_order": order,
                        "definition_en": syn.definition(),
                        "example_en": examples[0] if examples else None,
                    }
                )

        if not senses:
            continue

        words.append(
            {
                "id": word_id,
                "english": word,
                "frequency_rank": rank,
                "level": cefr_for_rank(rank),
                "source_list": "wordfreq+wordnet",
                "senses": senses,
            }
        )

    return words


def extract_json(text: str) -> str:
    """Strip Markdown fences so json.loads can parse the body."""
    text = text.strip()
    if text.startswith("```"):
        # ```json\n...\n```
        body = text.split("```", 2)
        if len(body) >= 2:
            inner = body[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner.strip()
            if text.endswith("```"):
                text = text[:-3].strip()
    return text


def translate_word(
    client, model: str, word: str, senses: list[dict]
) -> tuple[list[dict], list[str]]:
    """One Claude call per word: returns (senses_out, category_slugs).

    senses_out is a list of {"meaning", "example"} dicts in the same order
    as input. category_slugs is 1-3 entries from CATEGORY_SLUGS (others
    are filtered out by the caller's whitelist).
    """
    sense_lines = "\n".join(
        f"{i + 1}. ({s['part_of_speech']}) {s['definition_en']}"
        for i, s in enumerate(senses)
    )
    cats_str = ", ".join(CATEGORY_SLUGS)
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
        raise ValueError(f"expected JSON object, got: {type(parsed).__name__}")
    senses_out = parsed.get("senses", [])
    valid = set(CATEGORY_SLUGS)
    cats_out = [c for c in parsed.get("categories", []) if c in valid]
    return senses_out, cats_out


def sql_literal(s):
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


def write_words_sql(path: Path, words: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Openjam: English words + senses\n")
        f.write("-- Source: wordfreq (frequency) + Princeton WordNet 3.1 (senses, definitions, examples)\n")
        f.write("-- Auto-generated by scripts/generate_seed.py\n\n")
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

        f.write(
            "INSERT INTO word_senses "
            "(id, word_id, part_of_speech, sense_order, definition_en, example_en) VALUES\n"
        )
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
        f.write("-- Openjam: Persian (fa) translations of word senses\n")
        f.write("-- Generated by Claude with human review pending\n")
        f.write("-- Auto-generated by scripts/generate_seed.py\n\n")
        f.write("BEGIN;\n\n")
        f.write(
            "INSERT INTO sense_translations "
            "(id, sense_id, language_code, meaning, example) VALUES\n"
        )
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


def write_categories_sql(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"-- Openjam: category taxonomy ({len(CATEGORIES)} categories, flat).\n")
        f.write("-- Auto-generated by scripts/generate_seed.py\n\n")
        f.write("BEGIN;\n\n")
        f.write("INSERT INTO categories (id, slug, name_en) VALUES\n")
        rows = [
            "  ('{id}', {slug}, {name})".format(
                id=deterministic_uuid(f"cat:{slug}"),
                slug=sql_literal(slug),
                name=sql_literal(name),
            )
            for slug, name in CATEGORIES
        ]
        f.write(",\n".join(rows))
        f.write("\nON CONFLICT (slug) DO NOTHING;\n\n")
        f.write("COMMIT;\n")


def write_word_categories_sql(path: Path, word_categories: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Openjam: word <-> category assignments.\n")
        f.write("-- Auto-generated by scripts/generate_seed.py\n\n")
        if not word_categories:
            f.write("-- (no assignments yet)\n")
            return
        f.write("BEGIN;\n\n")
        f.write("INSERT INTO word_categories (word_id, category_id) VALUES\n")
        rows = [
            "  ('{word_id}', '{cat_id}')".format(
                word_id=wc["word_id"], cat_id=wc["category_id"]
            )
            for wc in word_categories
        ]
        f.write(",\n".join(rows))
        f.write("\nON CONFLICT (word_id, category_id) DO NOTHING;\n\n")
        f.write("COMMIT;\n")


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    load_env_file()
    target = int(os.environ.get("N_WORDS", "1000"))
    model = os.environ.get("OPENJAM_MODEL", "claude-haiku-4-5-20251001")
    do_translate = os.environ.get("SKIP_TRANSLATE") != "1"

    print(f"[1/5] Ensuring NLTK data is downloaded")
    ensure_nltk_data()

    print(f"[2/5] Collecting top {target} words via wordfreq + WordNet")
    words = collect_words(target)
    sense_count = sum(len(w["senses"]) for w in words)
    print(f"      -> {len(words)} words with {sense_count} senses")
    if not words:
        print("ERROR: no words collected", file=sys.stderr)
        return 1

    write_json(ROOT / "data/json/words_en.json", words)
    write_words_sql(ROOT / "data/sql/words_en.sql", words)
    print(f"      -> wrote data/json/words_en.json, data/sql/words_en.sql")

    print(f"[3/5] Writing category taxonomy ({len(CATEGORIES)} categories)")
    write_categories_sql(ROOT / "data/sql/categories.sql")
    write_json(
        ROOT / "data/json/categories.json",
        [
            {"id": deterministic_uuid(f"cat:{slug}"), "slug": slug, "name_en": name}
            for slug, name in CATEGORIES
        ],
    )
    print(f"      -> wrote data/sql/categories.sql, data/json/categories.json")

    if not do_translate:
        print("[4/5] SKIP_TRANSLATE=1, stopping before Claude calls")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    # --- Resume support ---
    # Load any existing translations and word-category assignments so we
    # skip Claude calls for words already fully covered by previous runs.
    translations_json_path = ROOT / "data/json/translations_fa.json"
    word_categories_json_path = ROOT / "data/json/word_categories.json"

    existing_translations: dict[str, dict] = {}
    if translations_json_path.exists():
        for t in json.loads(translations_json_path.read_text(encoding="utf-8")):
            existing_translations[t["sense_id"]] = t

    existing_word_cats: dict[str, set[str]] = {}
    if word_categories_json_path.exists():
        for wc in json.loads(word_categories_json_path.read_text(encoding="utf-8")):
            existing_word_cats.setdefault(wc["word_id"], set()).add(wc["category_id"])

    if existing_translations or existing_word_cats:
        print(
            f"      resume: {len(existing_translations)} existing translations, "
            f"{len(existing_word_cats)} words already categorized"
        )

    import anthropic

    client = anthropic.Anthropic()

    print(f"[4/5] Translating + categorizing via {model}")
    translations: list[dict] = list(existing_translations.values())
    word_categories: list[dict] = [
        {"word_id": wid, "category_id": cid}
        for wid, cids in existing_word_cats.items()
        for cid in cids
    ]
    failures: list[tuple[str, str]] = []
    skipped = 0
    called = 0
    start = time.time()

    for idx, w in enumerate(words, 1):
        senses_done = all(s["id"] in existing_translations for s in w["senses"])
        cats_done = w["id"] in existing_word_cats
        if senses_done and cats_done:
            skipped += 1
            continue

        called += 1
        try:
            sense_results, cat_slugs = translate_word(
                client, model, w["english"], w["senses"]
            )
        except Exception as exc:
            failures.append((w["english"], str(exc)))
            continue

        if not senses_done:
            for sense, res in zip(w["senses"], sense_results):
                if not isinstance(res, dict) or "meaning" not in res:
                    continue
                if sense["id"] in existing_translations:
                    continue
                trans = {
                    "id": deterministic_uuid(f"trans:{sense['id']}:fa"),
                    "sense_id": sense["id"],
                    "language_code": "fa",
                    "meaning": str(res["meaning"]).strip(),
                    "example": (
                        str(res["example"]).strip() if res.get("example") else None
                    ),
                }
                translations.append(trans)
                existing_translations[sense["id"]] = trans

        if not cats_done:
            cat_set = existing_word_cats.setdefault(w["id"], set())
            for slug in cat_slugs:
                cat_id = deterministic_uuid(f"cat:{slug}")
                if cat_id in cat_set:
                    continue
                word_categories.append({"word_id": w["id"], "category_id": cat_id})
                cat_set.add(cat_id)

        if called % CHECKPOINT_EVERY == 0 or idx == len(words):
            write_json(translations_json_path, translations)
            write_json(word_categories_json_path, word_categories)
            elapsed = time.time() - start
            remaining = len(words) - idx
            eta = (elapsed / called) * remaining if called else 0
            print(
                f"      [{idx}/{len(words)}] call#{called} skip#{skipped} "
                f"{w['english']:<20s} elapsed={elapsed:5.0f}s eta={eta:5.0f}s"
            )

    print(f"[5/5] Writing final SQL")
    write_translations_sql(ROOT / "data/sql/translations_fa.sql", translations)
    write_word_categories_sql(ROOT / "data/sql/word_categories.sql", word_categories)
    print(f"      -> wrote translations_fa.sql, word_categories.sql")

    print()
    print(
        f"DONE: {len(words)} words ({skipped} reused from previous run, "
        f"{called} API calls)"
    )
    print(
        f"      {len(translations)} translations, "
        f"{len(word_categories)} category assignments"
    )
    if failures:
        print(f"FAILURES: {len(failures)} words failed:")
        for w, err in failures[:10]:
            print(f"  - {w}: {err}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
