"""
Openjam seed generator.

Pipeline:
  1. Pull top-N most-frequent English words from `wordfreq` (modern corpus).
  2. Look up each word in Princeton WordNet (via NLTK) to get senses,
     part of speech, definition, and example sentence.
  3. Ask the Claude API for a Persian translation + Persian example per sense.
  4. Emit:
       data/json/words_en.json
       data/json/translations_fa.json
       data/sql/words_en.sql
       data/sql/translations_fa.sql

Idempotent: re-running produces the same UUIDs (uuid5 over a fixed namespace).

Environment:
  ANTHROPIC_API_KEY    required for the translation step
  N_WORDS              optional, default 1000
  OPENJAM_MODEL        optional, default claude-haiku-4-5-20251001

Usage:
  python scripts/generate_seed.py            # 1000 words
  N_WORDS=10 python scripts/generate_seed.py # quick smoke test
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

CHECKPOINT_EVERY = 25  # write JSON every N words during translation


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


def translate_word(client, model: str, word: str, senses: list[dict]) -> list[dict]:
    """Ask Claude for Persian meaning + example for each sense of a word."""
    sense_lines = "\n".join(
        f"{i + 1}. ({s['part_of_speech']}) {s['definition_en']}"
        for i, s in enumerate(senses)
    )
    prompt = f"""You translate English vocabulary to Persian (Farsi, fa-IR) for learners.

English word: "{word}"

Senses:
{sense_lines}

For each sense in the SAME ORDER, return:
- "meaning": the Persian translation (word or short phrase, NO explanation, NO English)
- "example": a short, natural Persian sentence that uses the meaning in context

Return ONLY a JSON array, nothing else, no prose, no Markdown fences:
[{{"meaning": "...", "example": "..."}}, ...]
"""

    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text
    parsed = json.loads(extract_json(raw))
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got: {type(parsed).__name__}")
    return parsed


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


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    load_env_file()
    target = int(os.environ.get("N_WORDS", "1000"))
    model = os.environ.get("OPENJAM_MODEL", "claude-haiku-4-5-20251001")
    do_translate = os.environ.get("SKIP_TRANSLATE") != "1"

    print(f"[1/4] Ensuring NLTK WordNet is downloaded")
    ensure_nltk_data()

    print(f"[2/4] Collecting top {target} words via wordfreq + WordNet")
    words = collect_words(target)
    sense_count = sum(len(w["senses"]) for w in words)
    print(f"      -> {len(words)} words with {sense_count} senses")

    if not words:
        print("ERROR: no words collected", file=sys.stderr)
        return 1

    words_json_path = ROOT / "data" / "json" / "words_en.json"
    write_json(words_json_path, words)
    write_words_sql(ROOT / "data" / "sql" / "words_en.sql", words)
    print(f"      -> wrote {words_json_path.relative_to(ROOT)}")
    print(f"      -> wrote data/sql/words_en.sql")

    if not do_translate:
        print("[3/4] SKIP_TRANSLATE=1, stopping before Claude calls")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    import anthropic

    client = anthropic.Anthropic()

    print(f"[3/4] Translating {sense_count} senses to Persian via {model}")
    translations: list[dict] = []
    translations_json_path = ROOT / "data" / "json" / "translations_fa.json"
    failures: list[tuple[str, str]] = []
    start = time.time()

    for idx, w in enumerate(words, 1):
        try:
            results = translate_word(client, model, w["english"], w["senses"])
        except Exception as exc:
            failures.append((w["english"], str(exc)))
            continue

        for sense, res in zip(w["senses"], results):
            if not isinstance(res, dict) or "meaning" not in res:
                continue
            translations.append(
                {
                    "id": deterministic_uuid(f"trans:{sense['id']}:fa"),
                    "sense_id": sense["id"],
                    "language_code": "fa",
                    "meaning": str(res["meaning"]).strip(),
                    "example": (str(res["example"]).strip() if res.get("example") else None),
                }
            )

        if idx % CHECKPOINT_EVERY == 0 or idx == len(words):
            write_json(translations_json_path, translations)
            elapsed = time.time() - start
            eta = (elapsed / idx) * (len(words) - idx)
            print(f"      [{idx}/{len(words)}] {w['english']:<20s} elapsed={elapsed:5.0f}s eta={eta:5.0f}s")

    print(f"[4/4] Writing SQL")
    write_translations_sql(ROOT / "data" / "sql" / "translations_fa.sql", translations)
    print(f"      -> wrote data/sql/translations_fa.sql")

    print()
    print(f"DONE: {len(words)} words, {sense_count} senses, {len(translations)} translations")
    if failures:
        print(f"FAILURES: {len(failures)} words failed translation:")
        for w, err in failures[:10]:
            print(f"  - {w}: {err}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
