# Openjam

> A free, open, MIT-licensed English vocabulary database with multilingual translations.

Openjam is a community-driven dataset of English words enriched with phonetics, CEFR level, frequency rank, topic categories, and translations into multiple languages. Every developer and every project can use it freely — no paywalls, no API keys, no restrictions.

The first consumer of Openjam is [Jamlex](https://github.com/amirj4m/jamlex), an English-learning app for the Persian-speaking diaspora.

## Why Openjam?

Most open vocabulary datasets are either incomplete, lack translations, or come with restrictive licenses. Openjam aims to be:

- **Free forever** — MIT license, including the data
- **Polysemy-aware** — separate senses for words with multiple meanings (`run`, `bank`, `light`, ...)
- **Learner-friendly** — CEFR levels (A1–C2), frequency ranks, example sentences
- **Truly multilingual** — translations attached to specific senses, not the surface form
- **Easy to consume** — SQL dump and JSON files today; REST API and Python package planned

## What's inside

| Table | Purpose |
|---|---|
| `words` | Canonical English lemma + frequency rank + CEFR level |
| `word_senses` | Each distinct meaning of a word (resolves polysemy) |
| `sense_translations` | Translation of a sense into a target language |
| `word_phonetics` | IPA + audio URL per regional variant (UK/US) |
| `categories` | Topic/domain lookup (technology, medical, nature, …) |
| `word_categories` | Many-to-many between words and categories |
| `dataset_meta` | Schema version, dataset version, attribution |

See [data/sql/schema.sql](data/sql/schema.sql) for the full DDL.

## Quick start

### Import the SQL dump (PostgreSQL 13+)

```bash
createdb openjam
psql openjam < data/sql/schema.sql
psql openjam < data/sql/categories.sql
psql openjam < data/sql/words_en.sql
psql openjam < data/sql/word_categories.sql
psql openjam < data/sql/translations_fa.sql
```

All files are idempotent (`ON CONFLICT DO NOTHING`) — safe to re-run.

### Use the JSON files

The same data will be exported to `data/json/` for non-PostgreSQL consumers.

### REST API

Planned for v0.2.0. See [api/README.md](api/README.md).

### Python package

Planned for v0.3.0. See [python/README.md](python/README.md).

## Roadmap

- **v0.1.0** — Schema, 1000 English lemmas with Persian translations
- **v0.2.0** — Scale to 5000 lemmas, 35-category taxonomy with auto-tagging
- **v0.3.0** — REST API + Cloudflare hosting + JSON exports as release artifacts
- **v0.4.0** — Python package on PyPI
- **v0.5.0** — More languages (German, Arabic, Spanish, French, Turkish, Japanese)
- **v0.6.0** — Audio pronunciation URLs (UK + US, hosted)
- **v1.0.0** — Full A1–C2 coverage (~30K words) with reviewed translations

## Data sources & attribution

Openjam's seed data is built only from openly licensed or original sources, so the result can be redistributed under MIT:

- **Word list & senses**: [Princeton WordNet 3.1](https://wordnet.princeton.edu/) (permissive, MIT-compatible)
- **Frequency ranks**: derived from openly licensed corpora
- **CEFR levels**: factual classification (uncopyrightable) cross-referenced with multiple public lists
- **Translations**: AI-generated (Claude, GPT) with human review by the community — original work, MIT-licensed
- **Phonetics**: open IPA sources

Per-release source details are tracked in [ATTRIBUTION.md](ATTRIBUTION.md).

## Contributing

We welcome:
- New language translations
- Review and correction of AI-generated translations
- Topic/category contributions
- Bug reports and schema feedback

Open an issue or PR. Contribution guide coming soon.

## Versioning

The dataset is versioned independently of the schema:

- **Schema version** (`dataset_meta.schema_version`) — bumps when the DDL changes
- **Dataset version** (`dataset_meta.dataset_version`) — bumps with every data release

After import, check with:

```sql
SELECT * FROM dataset_meta;
```

## License

[MIT](LICENSE). Use it for anything, commercial or otherwise.

---

Built in the open. Patches welcome.
