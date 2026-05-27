-- Openjam schema (v1.0.0)
-- An open, MIT-licensed English vocabulary database with multilingual translations.
-- Target: PostgreSQL 13+
-- Repository: https://github.com/amirj4m/openjam

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- words: one row per canonical English lemma.
-- A lemma is the dictionary headword (e.g., "run", not "running" or "ran").
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS words (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    english         TEXT NOT NULL UNIQUE,
    frequency_rank  INTEGER,            -- 1 = most common; NULL = unknown
    level           TEXT,               -- CEFR: A1, A2, B1, B2, C1, C2
    source_list     TEXT,               -- where the entry came from (e.g., 'wordnet', 'ngsl', 'manual')
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT words_english_lowercase CHECK (english = LOWER(english)),
    CONSTRAINT words_level_valid       CHECK (level IS NULL OR level IN ('A1','A2','B1','B2','C1','C2'))
);

CREATE INDEX IF NOT EXISTS idx_words_frequency_rank ON words (frequency_rank);
CREATE INDEX IF NOT EXISTS idx_words_level          ON words (level);
CREATE INDEX IF NOT EXISTS idx_words_source_list    ON words (source_list);

-- ---------------------------------------------------------------------------
-- word_senses: each distinct meaning of a word.
-- This is what resolves polysemy: "bank" (financial) and "bank" (riverside)
-- live as two separate senses under the same word.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_senses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    word_id         UUID NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    part_of_speech  TEXT NOT NULL,
    sense_order     INTEGER NOT NULL DEFAULT 1,  -- 1 = primary sense for this part_of_speech
    definition_en   TEXT NOT NULL,
    example_en      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (word_id, part_of_speech, sense_order),
    CONSTRAINT word_senses_pos_valid CHECK (part_of_speech IN (
        'noun','verb','adjective','adverb','pronoun',
        'preposition','conjunction','interjection','determiner','numeral'
    ))
);

CREATE INDEX IF NOT EXISTS idx_word_senses_word_id        ON word_senses (word_id);
CREATE INDEX IF NOT EXISTS idx_word_senses_part_of_speech ON word_senses (part_of_speech);

-- ---------------------------------------------------------------------------
-- sense_translations: translation of a single sense into a target language.
-- Tied to the sense, not the word, so polysemous translations stay accurate.
-- language_code follows BCP 47: 'fa', 'fa-AF' (Dari), 'pt-BR', etc.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sense_translations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sense_id        UUID NOT NULL REFERENCES word_senses(id) ON DELETE CASCADE,
    language_code   TEXT NOT NULL,
    meaning         TEXT NOT NULL,
    example         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (sense_id, language_code),
    CONSTRAINT sense_translations_language_code_format
        CHECK (language_code ~ '^[a-z]{2,3}(-[A-Z]{2})?$')
);

CREATE INDEX IF NOT EXISTS idx_sense_translations_sense_id      ON sense_translations (sense_id);
CREATE INDEX IF NOT EXISTS idx_sense_translations_language_code ON sense_translations (language_code);

-- ---------------------------------------------------------------------------
-- word_phonetics: pronunciation per regional variant.
-- Allows separate IPA + audio for UK vs US (and a 'general' fallback).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_phonetics (
    word_id     UUID NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    variant     TEXT NOT NULL,
    ipa         TEXT,
    audio_url   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (word_id, variant),
    CONSTRAINT word_phonetics_variant_valid CHECK (variant IN ('uk','us','general'))
);

-- ---------------------------------------------------------------------------
-- word_forms: inflected surface forms that map back to a lemma.
-- Examples: 'ran' -> 'run' (VBD), 'children' -> 'child' (NNS),
-- 'better' -> 'good' (JJR). Lets apps map any surface form to a
-- canonical word entry (essential for reading-mode lookups).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_forms (
    form        TEXT NOT NULL,
    word_id     UUID NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    form_type   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (form, word_id),
    CONSTRAINT word_forms_form_lowercase CHECK (form = LOWER(form))
);

CREATE INDEX IF NOT EXISTS idx_word_forms_form    ON word_forms (form);
CREATE INDEX IF NOT EXISTS idx_word_forms_word_id ON word_forms (word_id);

-- ---------------------------------------------------------------------------
-- categories: lookup table for topic/domain tagging.
-- Hierarchical via parent_id (e.g., "medical/anatomy" → parent = "medical").
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT NOT NULL UNIQUE,
    name_en     TEXT NOT NULL,
    parent_id   UUID REFERENCES categories(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT categories_slug_format CHECK (slug ~ '^[a-z0-9-]+$')
);

CREATE INDEX IF NOT EXISTS idx_categories_parent_id ON categories (parent_id);

-- ---------------------------------------------------------------------------
-- word_categories: many-to-many word <-> category.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_categories (
    word_id     UUID NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (word_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_word_categories_category_id ON word_categories (category_id);

-- ---------------------------------------------------------------------------
-- dataset_meta: schema version, dataset version, attribution, etc.
-- Consumers query this to know what they have.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dataset_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO dataset_meta (key, value) VALUES
    ('schema_version',     '1.0.0'),
    ('dataset_version',    '0.1.0'),
    ('license',            'MIT'),
    ('homepage',           'https://github.com/amirj4m/openjam'),
    ('source_attribution', 'WordNet 3.1 (Princeton), open frequency lists, AI-generated translations with human review')
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- updated_at trigger: keeps updated_at fresh on every UPDATE.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION openjam_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_words_updated_at ON words;
CREATE TRIGGER trg_words_updated_at
    BEFORE UPDATE ON words
    FOR EACH ROW EXECUTE FUNCTION openjam_set_updated_at();

DROP TRIGGER IF EXISTS trg_word_senses_updated_at ON word_senses;
CREATE TRIGGER trg_word_senses_updated_at
    BEFORE UPDATE ON word_senses
    FOR EACH ROW EXECUTE FUNCTION openjam_set_updated_at();

DROP TRIGGER IF EXISTS trg_sense_translations_updated_at ON sense_translations;
CREATE TRIGGER trg_sense_translations_updated_at
    BEFORE UPDATE ON sense_translations
    FOR EACH ROW EXECUTE FUNCTION openjam_set_updated_at();
