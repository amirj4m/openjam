# Openjam — App Integration Guide

A practical guide for building a vocabulary-learning app on top of the
Openjam public API. Written for developers of any framework
(Flutter, React Native, web, native iOS/Android).

- **API base URL:** `https://openjam.amirj4m.com`
- **Audio CDN:** `https://audio.openjam.amirj4m.com`
- **Source repo:** `https://github.com/amirj4m/openjam`
- **License:** MIT (data, code, audio — all free for commercial use)
- **Auth:** none. No API key. No signup.
- **Rate limit:** 60 requests / minute per IP. Edge-cached, very generous.
- **CORS:** open (`*`) — works from any web origin.

The API is read-only. Data is refreshed at every release tag.

---

## 1. Dataset at a glance (v0.7.0)

| Resource | Count |
|---|---|
| English lemmas (words) | 19,282 |
| Distinct senses (meanings) | 32,609 |
| Persian (`fa`) translations | 32,600 |
| Topical categories (flat) | 35 |
| Topical groups (hierarchical, for UX) | 12 |
| Word-category assignments | 38,761 |
| Inflected forms (ran→run, children→child, …) | 14,857 |
| Audio MP3s (US Joanna, Polly Neural) | 19,282 |
| IPA pronunciations | 17,359 (90% coverage) |

Per word you can ask for: senses, definitions (English), example
sentences (English + target language), Persian translation, IPA,
audio URL, category memberships, inflected surface forms.

---

## 2. Recommended app UX flow

This is what the API was designed around. You can deviate, but
following it is the path of least surprise.

```
┌────────────────────────────────────────────────────────────┐
│  Home screen                                               │
│  ────────────                                              │
│  Show the 12 category GROUPS (Food & drink, People &       │
│  family, Body & health, …). Each card shows name_fa +      │
│  total word count.                                         │
│  Source: GET /v1/categories/groups                         │
└────────────────────────────────────────────────────────────┘
                            │ tap
                            ▼
┌────────────────────────────────────────────────────────────┐
│  Group page (e.g. "غذا و نوشیدنی")                          │
│  ─────────────────────────────                              │
│  Show the group's 2-5 leaf categories with word counts.    │
│  ("غذا — 577 words", "نوشیدنی — 132 words")                  │
│  Already in the response above; no extra call needed.      │
└────────────────────────────────────────────────────────────┘
                            │ tap leaf
                            ▼
┌────────────────────────────────────────────────────────────┐
│  Leaf category page (e.g. "غذا")                            │
│  ────────────────────────────                               │
│  Show paginated word list with quick translation.          │
│  Source: GET /v1/categories/food/words?lang=fa&limit=50    │
└────────────────────────────────────────────────────────────┘
                            │ tap word
                            ▼
┌────────────────────────────────────────────────────────────┐
│  Word detail page (e.g. "apple")                            │
│  ────────────────────                                       │
│  Show English word + IPA + audio play button +              │
│  all senses with Persian translation + example sentences   │
│  + categories.                                              │
│  Source: GET /v1/words/apple                                │
└────────────────────────────────────────────────────────────┘
```

Plus three cross-cutting screens:

- **Search:** autocomplete + reverse lookup. `GET /v1/autocomplete`, `GET /v1/search`.
- **Reading mode:** user pastes/views English text; tapping any word
  (including inflected forms like `ran`, `children`) opens the lemma.
  Source: `GET /v1/words/:any-form` (auto-resolves via `word_forms`).
- **Daily word:** `GET /v1/random?level=A1&lang=fa`.

---

## 3. The 12 category groups (for the home screen)

```
1.  غذا و نوشیدنی          food, drink                       (709)
2.  افراد و خانواده         family, person, profession,
                            relationship                       (3,892)
3.  بدن و سلامت             body, health                       (2,013)
4.  طبیعت و حیوانات         animal, plant, nature              (1,854)
5.  خانه و اشیاء            home, clothing, tool
6.  مکان و سفر              place, country, city, travel,
                            vehicle
7.  کار و پول              work, money
8.  جامعه و فرهنگ          society, religion, media, arts
9.  علم و فناوری           science, technology
10. ورزش و فعالیت          sport, action, event
11. احساسات و توصیف         emotion, quality, abstract
12. زمان و آموزش           time, school
```

Pull the full list with counts:

```
GET https://openjam.amirj4m.com/v1/categories/groups
```

Response shape:

```json
{
  "groups": [
    {
      "slug": "food-drink",
      "name_en": "Food & drink",
      "name_fa": "غذا و نوشیدنی",
      "word_count": 709,
      "children": [
        { "slug": "food",  "name_en": "Food",  "name_fa": "غذا",     "word_count": 577 },
        { "slug": "drink", "name_en": "Drink", "name_fa": "نوشیدنی", "word_count": 132 }
      ]
    },
    ...
  ]
}
```

---

## 4. Endpoint reference

All responses are JSON. All endpoints support gzip. All are edge-cached
(`Cache-Control: public, max-age=300, s-maxage=300`).

### `GET /v1/meta`
Schema/dataset version, license, attribution. Use it to detect new
releases and re-sync your local cache.

### `GET /v1/categories/groups`
12 groups → 35 leaves, with Persian + English display names and
word counts. **This is the home-screen feed.**

### `GET /v1/categories`
Flat list of 35 categories with English names only and word counts.
Useful for advanced screens.

### `GET /v1/categories/:slug/words?lang=fa&limit=50&offset=0`
List of words in a category, ordered by frequency. With `lang=fa`
each word includes a `translations` array (per-sense Persian).

### `GET /v1/words/:english`
Full word payload. **Falls back to `word_forms`** — if you query
`ran`, you get the entry for `run` plus a `resolved_from` hint.

Response shape (abbreviated):
```json
{
  "id": "...",
  "english": "run",
  "frequency_rank": 309,
  "level": "A1",
  "senses": [
    {
      "part_of_speech": "verb",
      "definition_en": "move fast by using one's feet...",
      "example_en": "She likes to run in the morning.",
      "translations": [
        { "language_code": "fa", "meaning": "دویدن",
          "example": "او هر روز صبح می‌دود." }
      ]
    },
    ...
  ],
  "phonetics": [
    { "variant": "us", "ipa": "rən",
      "audio_url": "https://audio.openjam.amirj4m.com/us/run.mp3" }
  ],
  "categories": [
    { "slug": "action", "name_en": "Actions and movement" }
  ],
  "resolved_from": null
}
```

If queried with an inflected form:
```
GET /v1/words/ran
```
Response includes:
```json
"resolved_from": { "queried": "ran", "form_type": "past" }
```

### `GET /v1/words/:english/audio?variant=us` (or `uk`)
Returns a **302 redirect** to the MP3. Lets you use it directly in
`<audio src="...">` or a player. No JSON to parse.

### `GET /v1/words/:english/translations/:lang`
Just the per-sense translations for one language. Lighter than the
full word payload.

### `GET /v1/lookup?q=ran`
Lightweight form→lemma resolver. Returns the canonical lemma without
fetching the full word payload. Use this for tap-to-lookup in reading
mode if you don't need the full payload immediately.

### `GET /v1/search?q=...&from=en|fa&mode=prefix|contains|exact&limit=20`
Forward search (English) or **reverse** search (Persian → English).
Examples:
- `GET /v1/search?q=run&mode=prefix` → words starting with `run`
- `GET /v1/search?q=دویدن&from=fa&mode=contains` → English words with a Persian translation containing `دویدن`

### `GET /v1/autocomplete?q=ru&lang=en&limit=10`
Faster than `/search` for typeahead UIs. Returns just a flat list of
suggestions. Supports `lang=fa` too.

### `GET /v1/random?level=A1&lang=fa&category=food`
Random word matching the filters. Returns the full word payload.
Use for daily-word widgets, lesson generation, flashcards.

### `GET /v1/bulk/manifest`
Returns URLs to raw JSON dumps on GitHub. Use this for first-launch
sync (download once, store locally, refresh when dataset_version bumps).

### `GET /v1/bulk/category/:slug?lang=fa`
Full category payload (all words + senses + translations) in a single
response. Handy for "download this category for offline study".

---

## 5. Audio

```
https://audio.openjam.amirj4m.com/us/<word>.mp3
```

- Hosted on Cloudflare R2 with free egress (the consumer pays nothing).
- `Cache-Control: public, max-age=31536000, immutable` — browsers and
  OS players cache forever, so repeated plays are local.
- The convenience endpoint `/v1/words/:english/audio?variant=us`
  returns a 302 redirect to the same URL if you prefer to go through
  the API.

UK variant is on the roadmap. Until then, `variant=uk` returns 404.

---

## 6. Recommended client architecture (hybrid)

For a vocab-learning app, the right pattern is **bundled snapshot +
periodic sync + on-demand audio**.

### First launch
1. Fetch `GET /v1/bulk/manifest`.
2. Download the four JSON files listed in `files` (~15 MB total
   uncompressed, much less gzipped).
3. Load them into local SQLite (Drift / Floor / sqlite_async for Flutter).
4. Save the `dataset_version` from the manifest.

### Daily/weekly check
1. `GET /v1/meta` → compare `dataset_version` against local copy.
2. If newer, re-download from `/v1/bulk/manifest`.

### Runtime queries
1. **Browse / category list / word detail / search / autocomplete** →
   query your local SQLite. Zero network, instant.
2. **Audio** → stream on demand from the R2 URL. The OS handles
   caching; you can also prefetch the audio for the user's current
   study deck.
3. **Reverse lookup / inflection check** when you don't trust the
   local DB to be fresh → fall back to the live API.

Why hybrid: text data is small enough to bundle for instant offline
use; audio is too large to bundle (~90 MB) so stream-on-demand is the
right trade-off.

---

## 7. Sample code (Dart / Flutter, illustrative)

```dart
import 'package:http/http.dart' as http;
import 'package:audioplayers/audioplayers.dart';

const apiBase = 'https://openjam.amirj4m.com';

// Home screen: load category groups
final res = await http.get(Uri.parse('$apiBase/v1/categories/groups'));
final groups = jsonDecode(res.body)['groups'] as List;
for (final g in groups) {
  print('${g['name_fa']}  (${g['word_count']} words)');
}

// Word detail screen: load full word
final r = await http.get(Uri.parse('$apiBase/v1/words/run'));
final word = jsonDecode(r.body);
final ipa = word['phonetics'][0]['ipa'];           // "rən"
final audioUrl = word['phonetics'][0]['audio_url']; // https://audio…/run.mp3

// Play audio
final player = AudioPlayer();
await player.play(UrlSource(audioUrl));

// Reading mode: tap any word, even inflected
final r2 = await http.get(Uri.parse('$apiBase/v1/words/ran'));
final entry = jsonDecode(r2.body);
print(entry['english']);          // "run" (resolved from "ran")
print(entry['resolved_from']);    // { queried: "ran", form_type: "past" }
```

---

## 8. Things the developer doesn't need to worry about

- **API uptime:** Cloudflare Workers run at the edge worldwide. Free
  tier covers up to 100k requests/day.
- **Audio bandwidth costs:** R2 egress is free. Even at viral scale,
  zero cost.
- **DNS / SSL:** Both custom domains have automatic Cloudflare SSL.
- **Versioning:** every release is a git tag (`v0.7.0`). The
  `dataset_version` in `/v1/meta` always matches the data live on the
  API.
- **CORS:** allowed for all origins.

---

## 9. Things to watch for

- **Some words have no IPA** (~1900 / 19282 — rare or proper nouns).
  Handle `ipa: null` gracefully.
- **Audio is US only right now.** Don't hardcode `variant=us`; if you
  let users pick UK later, fall back to US when UK is missing.
- **Translations are AI-generated** (Claude Haiku 4.5) and not yet
  human-reviewed. Encourage user reports of bad translations; we can
  fix them in the next dataset release.
- **Rate limit is per-IP, 60 req/min.** A single user is well under
  this, but if you ever batch requests in a background job, slow down.

---

## 10. Where to ask for help

- Open an issue: https://github.com/amirj4m/openjam/issues
- License questions: it's MIT. Use freely. Attribution appreciated but
  not required.
