# Openjam REST API

Status: **Planned (v0.2.0)**

The REST API will provide query access to the Openjam dataset without requiring database setup. Until it ships, use the SQL dump or JSON files in [`../data/`](../data/).

## Planned endpoints

- `GET /v1/words?level=A1&limit=100` — list words
- `GET /v1/words/:english` — single word with all senses and translations
- `GET /v1/words/:english/translations/:lang` — single translation
- `GET /v1/categories` — list categories
- `GET /v1/random?level=B1` — random word (useful for daily-learning apps)

## Design principles

- Read-only and unauthenticated for public endpoints
- Reasonable rate limits per IP
- Caching headers so CDNs and consumers can stay efficient
- Stable URLs; breaking changes only at major version bumps

Follow the main repository for progress.
