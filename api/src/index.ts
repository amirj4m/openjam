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
      "GET /v1/words/:english (resolves inflections via word_forms)",
      "GET /v1/words/:english/audio?variant=us|uk",
      "GET /v1/words/:english/translations/:lang",
      "GET /v1/lookup?q=ran  (form -> lemma)",
      "GET /v1/random?level=B1&lang=fa&category=food",
      "GET /v1/categories/groups  (12 main groups -> 35 leaves, fa+en names)",
      "GET /v1/categories",
      "GET /v1/categories/:slug/words",
      "GET /v1/search?q=...&from=en|fa&mode=prefix|contains|exact&limit=20",
      "GET /v1/autocomplete?q=...&lang=en|fa&limit=10",
      "GET /v1/bulk/manifest",
      "GET /v1/bulk/category/:slug?lang=fa",
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

  const { results: phonetics } = await db
    .prepare(
      "SELECT variant, ipa, audio_url FROM word_phonetics WHERE word_id = ?",
    )
    .bind(word.id)
    .all<{ variant: string; ipa: string | null; audio_url: string | null }>();

  return {
    ...word,
    senses: senses.map((s) => ({
      ...s,
      translations: translationsBySense[s.id] ?? [],
    })),
    categories: cats,
    phonetics,
  };
}

// --- /v1/words/:english (full word; falls back to inflection lookup) ---
app.get("/v1/words/:english", async (c) => {
  const queryWord = c.req.param("english").toLowerCase();

  // Fast path: direct lemma hit.
  let payload = await fetchWordPayload(c.env.DB, queryWord);
  if (payload) return c.json(payload);

  // Fallback: maybe it's an inflected form (ran -> run, children -> child).
  const formMatch = await c.env.DB
    .prepare(
      `SELECT w.english, f.form_type
       FROM word_forms f
       JOIN words w ON w.id = f.word_id
       WHERE f.form = ?
       LIMIT 1`,
    )
    .bind(queryWord)
    .first<{ english: string; form_type: string | null }>();

  if (!formMatch) {
    return c.json({ error: "word not found", queried: queryWord }, 404);
  }

  payload = await fetchWordPayload(c.env.DB, formMatch.english);
  if (!payload) {
    return c.json({ error: "word not found", queried: queryWord }, 404);
  }
  // Tell the caller we redirected from an inflected form.
  return c.json({
    ...payload,
    resolved_from: { queried: queryWord, form_type: formMatch.form_type },
  });
});

// --- /v1/lookup (form -> lemma, lightweight) ---
// Cheap endpoint when the app only needs to know "what's the canonical word
// for 'ran'?" without fetching the full word payload.
app.get("/v1/lookup", async (c) => {
  const q = c.req.query("q")?.trim().toLowerCase();
  if (!q) return c.json({ error: "q parameter required" }, 400);

  // Exact lemma?
  const direct = await c.env.DB
    .prepare("SELECT english FROM words WHERE english = ?")
    .bind(q)
    .first<{ english: string }>();
  if (direct) {
    return c.json({ form: q, lemma: direct.english, form_type: "base", exact: true });
  }

  // Inflected form?
  const inflected = await c.env.DB
    .prepare(
      `SELECT w.english, f.form_type
       FROM word_forms f
       JOIN words w ON w.id = f.word_id
       WHERE f.form = ?`,
    )
    .bind(q)
    .all<{ english: string; form_type: string | null }>();

  if (!inflected.results.length) {
    return c.json({ error: "no match", queried: q }, 404);
  }

  // Multiple lemmas may share an inflection (rare). Return all.
  return c.json({
    form: q,
    exact: false,
    matches: inflected.results.map((m) => ({
      lemma: m.english,
      form_type: m.form_type,
    })),
  });
});

// --- /v1/words/:english/audio?variant=us|uk ---
// 302 redirects to the MP3 on R2. Lets apps use <audio src="..."> directly
// without doing a JSON fetch first.
app.get("/v1/words/:english/audio", async (c) => {
  const english = c.req.param("english").toLowerCase();
  const variant = c.req.query("variant") ?? "us";

  const row = await c.env.DB
    .prepare(
      `SELECT p.audio_url
       FROM word_phonetics p
       JOIN words w ON w.id = p.word_id
       WHERE w.english = ? AND p.variant = ?`,
    )
    .bind(english, variant)
    .first<{ audio_url: string | null }>();

  if (!row?.audio_url) {
    return c.json({ error: "audio not available", english, variant }, 404);
  }
  return c.redirect(row.audio_url, 302);
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

// 12 top-level groups that bucket the 35 flat categories. Designed for a
// vocab-learning home screen: small enough to show all at once, each group
// has 2-5 leaves so drilling in stays meaningful. name_fa is the Persian
// label apps in fa locale can show directly; name_en is the fallback.
const CATEGORY_GROUPS: {
  slug: string;
  name_en: string;
  name_fa: string;
  children: { slug: string; name_en: string; name_fa: string }[];
}[] = [
  {
    slug: "food-drink",
    name_en: "Food & drink",
    name_fa: "غذا و نوشیدنی",
    children: [
      { slug: "food", name_en: "Food", name_fa: "غذا" },
      { slug: "drink", name_en: "Drink", name_fa: "نوشیدنی" },
    ],
  },
  {
    slug: "people-family",
    name_en: "People & family",
    name_fa: "افراد و خانواده",
    children: [
      { slug: "family", name_en: "Family", name_fa: "خانواده" },
      { slug: "person", name_en: "People", name_fa: "مردم" },
      { slug: "profession", name_en: "Professions", name_fa: "شغل‌ها" },
      { slug: "relationship", name_en: "Relationships", name_fa: "روابط" },
    ],
  },
  {
    slug: "body-health",
    name_en: "Body & health",
    name_fa: "بدن و سلامت",
    children: [
      { slug: "body", name_en: "Body parts", name_fa: "اعضای بدن" },
      { slug: "health", name_en: "Health & medicine", name_fa: "سلامت و پزشکی" },
    ],
  },
  {
    slug: "nature-animals",
    name_en: "Nature & animals",
    name_fa: "طبیعت و حیوانات",
    children: [
      { slug: "animal", name_en: "Animals", name_fa: "حیوانات" },
      { slug: "plant", name_en: "Plants", name_fa: "گیاهان" },
      { slug: "nature", name_en: "Nature & weather", name_fa: "طبیعت و آب‌وهوا" },
    ],
  },
  {
    slug: "home-objects",
    name_en: "Home & objects",
    name_fa: "خانه و اشیاء",
    children: [
      { slug: "home", name_en: "Home", name_fa: "خانه" },
      { slug: "clothing", name_en: "Clothing", name_fa: "لباس" },
      { slug: "tool", name_en: "Tools & devices", name_fa: "ابزار و وسایل" },
    ],
  },
  {
    slug: "places-travel",
    name_en: "Places & travel",
    name_fa: "مکان و سفر",
    children: [
      { slug: "place", name_en: "Places", name_fa: "مکان‌ها" },
      { slug: "country", name_en: "Countries", name_fa: "کشورها" },
      { slug: "city", name_en: "Cities", name_fa: "شهرها" },
      { slug: "travel", name_en: "Travel", name_fa: "سفر" },
      { slug: "vehicle", name_en: "Vehicles & transport", name_fa: "وسایل نقلیه" },
    ],
  },
  {
    slug: "work-money",
    name_en: "Work & money",
    name_fa: "کار و پول",
    children: [
      { slug: "work", name_en: "Work & business", name_fa: "کار و کسب‌وکار" },
      { slug: "money", name_en: "Money & finance", name_fa: "پول و مالی" },
    ],
  },
  {
    slug: "society-culture",
    name_en: "Society & culture",
    name_fa: "جامعه و فرهنگ",
    children: [
      { slug: "society", name_en: "Society & politics", name_fa: "جامعه و سیاست" },
      { slug: "religion", name_en: "Religion", name_fa: "دین" },
      { slug: "media", name_en: "Media & communication", name_fa: "رسانه و ارتباطات" },
      { slug: "arts", name_en: "Arts & culture", name_fa: "هنر و فرهنگ" },
    ],
  },
  {
    slug: "science-tech",
    name_en: "Science & technology",
    name_fa: "علم و فناوری",
    children: [
      { slug: "science", name_en: "Science", name_fa: "علم" },
      { slug: "technology", name_en: "Technology", name_fa: "فناوری" },
    ],
  },
  {
    slug: "sport-action",
    name_en: "Sports & activity",
    name_fa: "ورزش و فعالیت",
    children: [
      { slug: "sport", name_en: "Sports & games", name_fa: "ورزش و بازی" },
      { slug: "action", name_en: "Actions & movement", name_fa: "اعمال و حرکت" },
      { slug: "event", name_en: "Events", name_fa: "رویدادها" },
    ],
  },
  {
    slug: "feelings-qualities",
    name_en: "Feelings & qualities",
    name_fa: "احساسات و توصیف",
    children: [
      { slug: "emotion", name_en: "Emotions", name_fa: "احساسات" },
      { slug: "quality", name_en: "Qualities & descriptions", name_fa: "ویژگی‌ها" },
      { slug: "abstract", name_en: "Abstract concepts", name_fa: "مفاهیم انتزاعی" },
    ],
  },
  {
    slug: "time-learning",
    name_en: "Time & learning",
    name_fa: "زمان و آموزش",
    children: [
      { slug: "time", name_en: "Time", name_fa: "زمان" },
      { slug: "school", name_en: "School & education", name_fa: "آموزش" },
    ],
  },
];

// --- /v1/categories/groups (hierarchical: 12 groups -> 35 leaves) ---
// Built for vocab-learning UX: home screen shows the 12 groups; tap a group
// to reveal its 2-5 leaf categories with their word counts.
app.get("/v1/categories/groups", async (c) => {
  const { results: counts } = await c.env.DB
    .prepare(
      `SELECT c.slug, COUNT(wc.word_id) AS word_count
       FROM categories c
       LEFT JOIN word_categories wc ON wc.category_id = c.id
       GROUP BY c.id`,
    )
    .all<{ slug: string; word_count: number }>();
  const countBySlug = new Map(counts.map((r) => [r.slug, r.word_count]));

  const groups = CATEGORY_GROUPS.map((g) => {
    const children = g.children.map((leaf) => ({
      slug: leaf.slug,
      name_en: leaf.name_en,
      name_fa: leaf.name_fa,
      word_count: countBySlug.get(leaf.slug) ?? 0,
    }));
    const total = children.reduce((s, l) => s + l.word_count, 0);
    return {
      slug: g.slug,
      name_en: g.name_en,
      name_fa: g.name_fa,
      word_count: total,
      children,
    };
  });

  return c.json({ groups });
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

// --- /v1/search (English prefix/contains, or reverse from any language) ---
app.get("/v1/search", async (c) => {
  const q = c.req.query("q")?.trim();
  if (!q) return c.json({ error: "q parameter required" }, 400);

  const from = (c.req.query("from") ?? "en").toLowerCase();
  const mode = c.req.query("mode") ?? "prefix";
  const limit = Math.min(parseInt(c.req.query("limit") ?? "20", 10), 100);

  if (mode !== "prefix" && mode !== "contains" && mode !== "exact") {
    return c.json({ error: "mode must be prefix, contains, or exact" }, 400);
  }

  const pattern =
    mode === "exact" ? q : mode === "contains" ? `%${q}%` : `${q}%`;
  const op = mode === "exact" ? "=" : "LIKE";

  if (from === "en") {
    const { results } = await c.env.DB
      .prepare(
        `SELECT english, frequency_rank, level
         FROM words
         WHERE english ${op} ?
         ORDER BY frequency_rank IS NULL, frequency_rank ASC
         LIMIT ?`,
      )
      .bind(pattern.toLowerCase(), limit)
      .all();
    return c.json({ query: q, from, mode, count: results.length, results });
  }

  // Reverse search: query against sense_translations.meaning for given language.
  const { results } = await c.env.DB
    .prepare(
      `SELECT DISTINCT w.english, w.frequency_rank, w.level,
              t.meaning AS matched_meaning,
              s.part_of_speech, s.definition_en
       FROM sense_translations t
       JOIN word_senses s ON s.id = t.sense_id
       JOIN words w ON w.id = s.word_id
       WHERE t.language_code = ? AND t.meaning ${op} ?
       ORDER BY w.frequency_rank IS NULL, w.frequency_rank ASC
       LIMIT ?`,
    )
    .bind(from, pattern, limit)
    .all();
  return c.json({ query: q, from, mode, count: results.length, results });
});

// --- /v1/autocomplete (fast prefix suggestions) ---
app.get("/v1/autocomplete", async (c) => {
  const q = c.req.query("q")?.trim();
  if (!q) return c.json({ error: "q parameter required" }, 400);

  const lang = (c.req.query("lang") ?? "en").toLowerCase();
  const limit = Math.min(parseInt(c.req.query("limit") ?? "10", 10), 50);

  if (lang === "en") {
    const { results } = await c.env.DB
      .prepare(
        `SELECT english
         FROM words
         WHERE english LIKE ?
         ORDER BY frequency_rank IS NULL, frequency_rank ASC
         LIMIT ?`,
      )
      .bind(`${q.toLowerCase()}%`, limit)
      .all<{ english: string }>();
    return c.json({ suggestions: results.map((r) => r.english) });
  }

  const { results } = await c.env.DB
    .prepare(
      `SELECT DISTINCT t.meaning
       FROM sense_translations t
       WHERE t.language_code = ? AND t.meaning LIKE ?
       LIMIT ?`,
    )
    .bind(lang, `${q}%`, limit)
    .all<{ meaning: string }>();
  return c.json({ suggestions: results.map((r) => r.meaning) });
});

// --- /v1/bulk/manifest (points to GitHub raw URLs for bulk download) ---
app.get("/v1/bulk/manifest", async (c) => {
  const ghRaw = "https://raw.githubusercontent.com/amirj4m/openjam/main/data/json";
  const meta = await c.env.DB
    .prepare("SELECT value FROM dataset_meta WHERE key = 'dataset_version'")
    .first<{ value: string }>();
  return c.json({
    dataset_version: meta?.value ?? c.env.DATASET_VERSION,
    note: "Bulk files live in the public GitHub repo. Fetch directly for first-launch sync.",
    files: {
      words: `${ghRaw}/words_en.json`,
      categories: `${ghRaw}/categories.json`,
      word_categories: `${ghRaw}/word_categories.json`,
      translations: {
        fa: `${ghRaw}/translations_fa.json`,
      },
    },
    sync_strategy: {
      first_launch: "Fetch all files in `files`, store locally, record dataset_version.",
      check_for_updates: "Periodically GET /v1/meta; if dataset_version > local, refetch.",
    },
  });
});

// --- /v1/bulk/category/:slug (whole category with senses+translations in one response) ---
// Uses a single JOIN query to avoid D1's per-statement parameter limit (~100).
app.get("/v1/bulk/category/:slug", async (c) => {
  const slug = c.req.param("slug");
  const lang = c.req.query("lang") ?? "fa";

  const cat = await c.env.DB
    .prepare("SELECT id, slug, name_en FROM categories WHERE slug = ?")
    .bind(slug)
    .first<{ id: string; slug: string; name_en: string }>();
  if (!cat) return c.json({ error: "category not found", slug }, 404);

  const { results: rows } = await c.env.DB
    .prepare(
      `SELECT
         w.id AS word_id, w.english, w.frequency_rank, w.level,
         s.id AS sense_id, s.part_of_speech, s.sense_order,
         s.definition_en, s.example_en,
         t.meaning, t.example
       FROM words w
       JOIN word_categories wc ON wc.word_id = w.id
       JOIN word_senses s ON s.word_id = w.id
       LEFT JOIN sense_translations t
         ON t.sense_id = s.id AND t.language_code = ?
       WHERE wc.category_id = ?
       ORDER BY w.frequency_rank IS NULL, w.frequency_rank ASC,
                s.part_of_speech, s.sense_order`,
    )
    .bind(lang, cat.id)
    .all<{
      word_id: string;
      english: string;
      frequency_rank: number | null;
      level: string | null;
      sense_id: string;
      part_of_speech: string;
      sense_order: number;
      definition_en: string;
      example_en: string | null;
      meaning: string | null;
      example: string | null;
    }>();

  // Pivot the flat join into nested words -> senses -> translations.
  const wordMap = new Map<string, {
    id: string;
    english: string;
    frequency_rank: number | null;
    level: string | null;
    senses: Map<string, {
      id: string;
      part_of_speech: string;
      sense_order: number;
      definition_en: string;
      example_en: string | null;
      translations: { meaning: string; example: string | null }[];
    }>;
  }>();

  for (const r of rows) {
    let w = wordMap.get(r.word_id);
    if (!w) {
      w = {
        id: r.word_id,
        english: r.english,
        frequency_rank: r.frequency_rank,
        level: r.level,
        senses: new Map(),
      };
      wordMap.set(r.word_id, w);
    }
    let s = w.senses.get(r.sense_id);
    if (!s) {
      s = {
        id: r.sense_id,
        part_of_speech: r.part_of_speech,
        sense_order: r.sense_order,
        definition_en: r.definition_en,
        example_en: r.example_en,
        translations: [],
      };
      w.senses.set(r.sense_id, s);
    }
    if (r.meaning) {
      s.translations.push({ meaning: r.meaning, example: r.example });
    }
  }

  const words = Array.from(wordMap.values()).map((w) => ({
    id: w.id,
    english: w.english,
    frequency_rank: w.frequency_rank,
    level: w.level,
    senses: Array.from(w.senses.values()),
  }));

  return c.json({
    category: cat,
    language: lang,
    word_count: words.length,
    words,
  });
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
