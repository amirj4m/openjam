# SQL data files

| File | Purpose |
|---|---|
| `schema.sql` | DDL for the Openjam database (PostgreSQL 13+) |
| `words_en.sql` | English lemmas + senses *(coming in v0.1.0)* |
| `translations_fa.sql` | Persian translations *(coming in v0.1.0)* |

## Import order

```bash
psql openjam < schema.sql
psql openjam < words_en.sql
psql openjam < translations_fa.sql
```

Always import `schema.sql` first. Translation files depend on `word_senses` rows existing.
