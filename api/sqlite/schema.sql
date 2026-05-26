-- Openjam D1 (SQLite) schema, v1.0.0
-- Mirrors the canonical Postgres schema in data/sql/schema.sql with
-- SQLite-friendly types and no Postgres-specific features (regex CHECK,
-- TIMESTAMPTZ, gen_random_uuid). UUIDs come from the build script.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS words (
    id              TEXT PRIMARY KEY,
    english         TEXT NOT NULL UNIQUE,
    frequency_rank  INTEGER,
    level           TEXT CHECK (level IS NULL OR level IN ('A1','A2','B1','B2','C1','C2')),
    source_list     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_words_frequency_rank ON words (frequency_rank);
CREATE INDEX IF NOT EXISTS idx_words_level          ON words (level);
CREATE INDEX IF NOT EXISTS idx_words_source_list    ON words (source_list);

CREATE TABLE IF NOT EXISTS word_senses (
    id              TEXT PRIMARY KEY,
    word_id         TEXT NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    part_of_speech  TEXT NOT NULL CHECK (part_of_speech IN (
        'noun','verb','adjective','adverb','pronoun',
        'preposition','conjunction','interjection','determiner','numeral'
    )),
    sense_order     INTEGER NOT NULL DEFAULT 1,
    definition_en   TEXT NOT NULL,
    example_en      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (word_id, part_of_speech, sense_order)
);

CREATE INDEX IF NOT EXISTS idx_word_senses_word_id        ON word_senses (word_id);
CREATE INDEX IF NOT EXISTS idx_word_senses_part_of_speech ON word_senses (part_of_speech);

CREATE TABLE IF NOT EXISTS sense_translations (
    id              TEXT PRIMARY KEY,
    sense_id        TEXT NOT NULL REFERENCES word_senses(id) ON DELETE CASCADE,
    language_code   TEXT NOT NULL,
    meaning         TEXT NOT NULL,
    example         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (sense_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_sense_translations_sense_id      ON sense_translations (sense_id);
CREATE INDEX IF NOT EXISTS idx_sense_translations_language_code ON sense_translations (language_code);

CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name_en     TEXT NOT NULL,
    parent_id   TEXT REFERENCES categories(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_categories_parent_id ON categories (parent_id);

CREATE TABLE IF NOT EXISTS word_categories (
    word_id     TEXT NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (word_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_word_categories_category_id ON word_categories (category_id);

CREATE TABLE IF NOT EXISTS dataset_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
