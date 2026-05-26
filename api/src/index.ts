/**
 * Openjam REST API on Cloudflare Workers + D1.
 *
 * Read-only, public, unauthenticated. Edge-cached by Cloudflare.
 * Data comes from the Openjam dataset (https://github.com/amirj4m/openjam)
 * mirrored into D1 via `scripts/api/sqlite/build-data.py`.
 */

import { Hono } from "hono";
import { cors } from "hono/cors";

type Bindings = {
  DB: D1Database;
  RATE_LIMITER: { limit: (opts: { key: string }) => Promise<{ success: boolean }> };
  DATASET_VERSION: string;
  SCHEMA_VERSION: string;
};

const app = new Hono<{ Bindings: Bindings }>();

app.use(
  "*",
  cors({
    origin: "*",
    allowMethods: ["GET", "OPTIONS"],
    maxAge: 86400,
  }),
);

// Per-IP rate limit: 60 req/min. Legitimate apps fit comfortably; bots
// and scrapers get 429. The limiter is a free Cloudflare primitive.
app.use("/v1/*", async (c, next) => {
  const ip = c.req.header("CF-Connecting-IP") ?? "anonymous";
  const { success } = await c.env.RATE_LIMITER.limit({ key: ip });
  if (!success) {
    return c.json(
      {
        error: "rate_limited",
        message: "Too many requests. Limit is 60 per minute per IP.",
      },
      429,
    );
  }
  await next();
});

// Cache responses at the edge for 5 minutes; long enough to be useful,
// short enough that a dataset re-deploy is reflected quickly.
app.use("/v1/*", async (c, next) => {
  await next();
  c.header("Cache-Control", "public, max-age=300, s-maxage=300");
});

// --- Root: human-readable index of the API ---
app.get("/", (c) =>
  c.json({
    name: "Openjam API",
    description:
      "Open multilingual English vocabulary database, MIT-licensed.",
    homepage: "https://github.com/amirj4m/openjam",
    dataset_version: c.env.DATASET_VERSION,
    schema_version: c.env.SCHEMA_VERSION,
    license: "MIT",
    endpoints: [
      "GET /v1/meta",
      "GET /v1/words?level=A1&lang=fa&category=food&limit=100&offset=0",
      "GET /v1/words/:english",
      "GET /v1/words/:english/translations/:lang",
      "GET /v1/random?level=B1&lang=fa&category=food",
      "GET /v1/categories",
      "GET /v1/categories/:slug/words",
    ],
  }),
);

// --- /v1/meta ---
app.get("/v1/meta", async (c) => {
  const { results } = await c.env.DB.prepare(
    "SELECT key, value FROM dataset_meta",
  ).all<{ key: string; value: string }>();
  const meta = Object.fromEntries(results.map((r) => [r.key, r.value]));
  return c.json(meta);
});

// --- /v1/words (list with filters) ---
app.get("/v1/words", async (c) => {
  const level = c.req.query("level");
  const lang = c.req.query("lang");
  const category = c.req.query("category");
  const limit = Math.min(parseInt(c.req.query("limit") || "50", 10), 500);
  const offset = parseInt(c.req.query("offset") || "0", 10);

  const wheres: string[] = [];
  const params: unknown[] = [];
  let from = "FROM words w";

  if (category) {
    from +=
      " JOIN word_categories wc ON wc.word_id = w.id" +
      " JOIN categories c ON c.id = wc.category_id";
    wheres.push("c.slug = ?");
    params.push(category);
  }
  if (level) {
    wheres.push("w.level = ?");
    params.push(level);
  }

  const whereSql = wheres.length ? "WHERE " + wheres.join(" AND ") : "";

  const countRow = await c.env.DB
    .prepare(`SELECT COUNT(DISTINCT w.id) AS total ${from} ${whereSql}`)
    .bind(...params)
    .first<{ total: number }>();
  const total = countRow?.total ?? 0;

  const { results: wordRows } = await c.env.DB
    .prepare(
      `SELECT DISTINCT w.id, w.english, w.frequency_rank, w.level
       ${from} ${whereSql}
       ORDER BY w.frequency_rank IS NULL, w.frequency_rank ASC
       LIMIT ? OFFSET ?`,
    )
    .bind(...params, limit, offset)
    .all<{
      id: string;
      english: string;
      frequency_rank: number | null;
      level: string | null;
    }>();

  // Optional: translations + categories per word when lang is given.
  let translationMap: Record<string, unknown[]> = {};
  if (lang && wordRows.length) {
    const ids = wordRows.map((w) => w.id);
    const placeholders = ids.map(() => "?").join(",");
    const { results: tRows } = await c.env.DB
      .prepare(
        `SELECT t.sense_id, t.meaning, t.example,
                s.word_id, s.part_of_speech, s.sense_order, s.definition_en
         FROM sense_translations t
         JOIN word_senses s ON s.id = t.sense_id
         WHERE s.word_id IN (${placeholders}) AND t.language_code = ?
         ORDER BY s.part_of_speech, s.sense_order`,
      )
      .bind(...ids, lang)
      .all<{
        sense_id: string;
        meaning: string;
        example: string | null;
        word_id: string;
        part_of_speech: string;
        sense_order: number;
        definition_en: string;
      }>();
    translationMap = {};
    for (const t of tRows) {
      (translationMap[t.word_id] ??= []).push({
        sense_id: t.sense_id,
        part_of_speech: t.part_of_speech,
        sense_order: t.sense_order,
        definition_en: t.definition_en,
        meaning: t.meaning,
        example: t.example,
      });
    }
  }

  const data = wordRows.map((w) => ({
    id: w.id,
    english: w.english,
    frequency_rank: w.frequency_rank,
    level: w.level,
    ...(lang ? { translations: translationMap[w.id] ?? [] } : {}),
  }));

  return c.json({
    data,
    meta: { total, limit, offset, filters: { level, lang, category } },
  });
});

// Helper: full word payload (used by /v1/words/:english and /v1/random)
async function fetchWordPayload(db: D1Database, english: string) {
  const word = await db
    .prepare(
      "SELECT id, english, frequency_rank, level, source_list FROM words WHERE english = ?",
    )
    .bind(english)
    .first<{
      id: string;
      english: string;
      frequency_rank: number | null;
      level: string | null;
      source_list: string | null;
    }>();
  if (!word) return null;

  const { results: senses } = await db
    .prepare(
      `SELECT id, part_of_speech, sense_order, definition_en, example_en
       FROM word_senses
       WHERE word_id = ?
       ORDER BY part_of_speech, sense_order`,
    )
    .bind(word.id)
    .all<{
      id: string;
      part_of_speech: string;
      sense_order: number;
      definition_en: string;
      example_en: string | null;
    }>();

  let translationsBySense: Record<string, unknown[]> = {};
  if (senses.length) {
    const senseIds = senses.map((s) => s.id);
    const ph = senseIds.map(() => "?").join(",");
    const { results: trs } = await db
      .prepare(
        `SELECT sense_id, language_code, meaning, example
         FROM sense_translations WHERE sense_id IN (${ph})`,
      )
      .bind(...senseIds)
      .all<{
        sense_id: string;
        language_code: string;
        meaning: string;
        example: string | null;
      }>();
    translationsBySense = {};
    for (const t of trs) {
      (translationsBySense[t.sense_id] ??= []).push({
        language_code: t.language_code,
        meaning: t.meaning,
        example: t.example,
      });
    }
  }

  const { results: cats } = await db
    .prepare(
      `SELECT c.slug, c.name_en FROM categories c
       JOIN word_categories wc ON wc.category_id = c.id
       WHERE wc.word_id = ?`,
    )
    .bind(word.id)
    .all<{ slug: string; name_en: string }>();

  return {
    ...word,
    senses: senses.map((s) => ({
      ...s,
      translations: translationsBySense[s.id] ?? [],
    })),
    categories: cats,
  };
}

// --- /v1/words/:english (full word) ---
app.get("/v1/words/:english", async (c) => {
  const english = c.req.param("english").toLowerCase();
  const payload = await fetchWordPayload(c.env.DB, english);
  if (!payload) return c.json({ error: "word not found", english }, 404);
  return c.json(payload);
});

// --- /v1/words/:english/translations/:lang ---
app.get("/v1/words/:english/translations/:lang", async (c) => {
  const english = c.req.param("english").toLowerCase();
  const lang = c.req.param("lang");

  const { results } = await c.env.DB
    .prepare(
      `SELECT s.part_of_speech, s.sense_order, s.definition_en, s.example_en,
              t.meaning, t.example
       FROM words w
       JOIN word_senses s ON s.word_id = w.id
       JOIN sense_translations t ON t.sense_id = s.id
       WHERE w.english = ? AND t.language_code = ?
       ORDER BY s.part_of_speech, s.sense_order`,
    )
    .bind(english, lang)
    .all();

  if (!results.length) {
    return c.json({ error: "no translations found", english, lang }, 404);
  }
  return c.json({ english, language: lang, senses: results });
});

// --- /v1/random ---
app.get("/v1/random", async (c) => {
  const level = c.req.query("level");
  const category = c.req.query("category");

  const wheres: string[] = [];
  const params: unknown[] = [];
  let from = "FROM words w";
  if (category) {
    from +=
      " JOIN word_categories wc ON wc.word_id = w.id" +
      " JOIN categories c ON c.id = wc.category_id";
    wheres.push("c.slug = ?");
    params.push(category);
  }
  if (level) {
    wheres.push("w.level = ?");
    params.push(level);
  }
  const whereSql = wheres.length ? "WHERE " + wheres.join(" AND ") : "";

  const row = await c.env.DB
    .prepare(`SELECT w.english ${from} ${whereSql} ORDER BY RANDOM() LIMIT 1`)
    .bind(...params)
    .first<{ english: string }>();

  if (!row) return c.json({ error: "no matching word" }, 404);

  const payload = await fetchWordPayload(c.env.DB, row.english);
  return c.json(payload);
});

// --- /v1/categories ---
app.get("/v1/categories", async (c) => {
  const { results } = await c.env.DB
    .prepare(
      `SELECT c.slug, c.name_en, COUNT(wc.word_id) AS word_count
       FROM categories c
       LEFT JOIN word_categories wc ON wc.category_id = c.id
       GROUP BY c.id
       ORDER BY word_count DESC`,
    )
    .all();
  return c.json({ data: results });
});

// --- /v1/categories/:slug/words ---
app.get("/v1/categories/:slug/words", async (c) => {
  const slug = c.req.param("slug");
  const limit = Math.min(parseInt(c.req.query("limit") || "50", 10), 500);
  const offset = parseInt(c.req.query("offset") || "0", 10);
  const lang = c.req.query("lang");

  const { results: wordRows } = await c.env.DB
    .prepare(
      `SELECT w.id, w.english, w.frequency_rank, w.level
       FROM words w
       JOIN word_categories wc ON wc.word_id = w.id
       JOIN categories c ON c.id = wc.category_id
       WHERE c.slug = ?
       ORDER BY w.frequency_rank IS NULL, w.frequency_rank ASC
       LIMIT ? OFFSET ?`,
    )
    .bind(slug, limit, offset)
    .all<{
      id: string;
      english: string;
      frequency_rank: number | null;
      level: string | null;
    }>();

  let translationMap: Record<string, unknown[]> = {};
  if (lang && wordRows.length) {
    const ids = wordRows.map((w) => w.id);
    const ph = ids.map(() => "?").join(",");
    const { results: tRows } = await c.env.DB
      .prepare(
        `SELECT t.meaning, t.example, s.word_id, s.part_of_speech, s.sense_order
         FROM sense_translations t
         JOIN word_senses s ON s.id = t.sense_id
         WHERE s.word_id IN (${ph}) AND t.language_code = ?
         ORDER BY s.part_of_speech, s.sense_order`,
      )
      .bind(...ids, lang)
      .all<{
        meaning: string;
        example: string | null;
        word_id: string;
        part_of_speech: string;
        sense_order: number;
      }>();
    for (const t of tRows) {
      (translationMap[t.word_id] ??= []).push({
        part_of_speech: t.part_of_speech,
        sense_order: t.sense_order,
        meaning: t.meaning,
        example: t.example,
      });
    }
  }

  const data = wordRows.map((w) => ({
    english: w.english,
    frequency_rank: w.frequency_rank,
    level: w.level,
    ...(lang ? { translations: translationMap[w.id] ?? [] } : {}),
  }));

  return c.json({ category: slug, data });
});

app.notFound((c) => c.json({ error: "not found", path: c.req.path }, 404));

app.onError((err, c) => {
  console.error(err);
  return c.json(
    { error: "internal error", message: err.message ?? String(err) },
    500,
  );
});

export default app;
