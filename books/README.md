# Books — curated vocabulary lists

This directory is an **optional, isolated** layer on top of the Openjam
core dataset. It holds vocabulary lists drawn from popular English
study books and standardized tests.

> **It is not part of the core dataset.** You can delete this directory
> and the matching D1 tables (`book_lists`, `book_list_words`) without
> affecting anything in `data/` or the rest of the API.

## What's here (v0.8.0)

| Book / list | Slug | Words | Notes |
|---|---|---|---|
| ۵۰۴ Essential Words for the GRE / TOEFL | `504-essential` | 504 | Classic Persian-Iranian study book. Foundational B1-B2 vocab. |
| IELTS Academic Word List (AWL) + general | `ielts` | ~600 | Coxhead's AWL plus common IELTS prep additions. |
| TOEFL high-frequency | `toefl` | ~1000 | Common TOEFL vocabulary, overlaps with IELTS. |
| GRE high-frequency | `gre` | ~800 | Curated GRE word list, advanced vocabulary. |

## Why isolated?

- **Books are exam-prep curation.** Categories in the core dataset
  (`food`, `body`, `nature`, ...) are *topical*. Mixing the two would
  pollute browsing.
- **Books may change.** Lists get curated, added, or removed.
  Keeping them separate means a curator can refresh a book without
  touching core data.
- **Books may be removed.** If a user doesn't want exam vocab in
  their app, they can ignore (or fully drop) this layer.

## How books reference the core dataset

Each book is just a **list of English lemmas** in a particular order.
At build time, `scripts/books/build_books.py`:

1. For each word in the book:
   - If the lemma exists in core `words` table → store the `word_id` reference.
   - If not → add the word to the core dataset (via the same generation pipeline that built openjam), then reference it.
2. Books gain all of openjam's enrichment for free: senses, Persian
   translations, audio, IPA, categories, inflections.
3. When the core dataset is refreshed in a later release, books
   automatically pick up the better data.

## API

```
GET /v1/books                       list all books
GET /v1/books/:slug                 meta for one book
GET /v1/books/:slug/words?lang=fa   paginated words with translations
```

See the main `INTEGRATION.md` for full app-integration guidance.

## How to add a new book

1. Create `books/<slug>/meta.json` with the book's metadata.
2. Create `books/<slug>/words.json` with the word list.
3. Run `python scripts/books/build_books.py --slug <slug>` to
   integrate it with the core dataset and emit SQL.
4. Apply the generated SQL to D1.
5. The new book is now live at `/v1/books/<slug>`.

## How to drop a book entirely

1. Delete `books/<slug>/` from this repo.
2. `DELETE FROM book_lists WHERE slug = '<slug>';`
3. `DELETE FROM book_list_words WHERE book_slug = '<slug>';`

Or, to drop all books:

```sql
DROP TABLE book_list_words;
DROP TABLE book_lists;
```

…and `rm -rf books/`. The core dataset is unaffected.

## File formats

### `meta.json`

```json
{
  "slug": "504-essential",
  "name_en": "504 Essential Words",
  "name_fa": "۵۰۴ لغت ضروری",
  "description": "A foundational list of essential English words, popular in Persian-Iranian study tradition.",
  "source_attribution": "Word list compiled from public study materials. Definitions and examples are original (generated for this dataset).",
  "level": "intermediate",
  "has_groups": true,
  "group_label": "Lesson",
  "group_label_fa": "درس"
}
```

### `words.json`

```json
{
  "version": "1.0",
  "words": [
    { "english": "abandon", "group": "lesson-1", "order": 1 },
    { "english": "abolish", "group": "lesson-1", "order": 2 },
    { "english": "absurd",  "group": "lesson-1", "order": 3 }
  ]
}
```

`group` is optional; books without lessons may omit it.
