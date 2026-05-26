# Openjam REST API

Public, read-only HTTP API for the Openjam multilingual vocabulary database.

- Stack: Cloudflare Workers + D1 + Hono
- Live URL (after deploy): `https://openjam.amirj4m.com`
- Source data: see [`../data/`](../data/)

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/` | API metadata + index of endpoints |
| GET | `/v1/meta` | Schema/dataset version, license, attribution |
| GET | `/v1/words?level=A1&lang=fa&category=food&limit=100&offset=0` | List with filters |
| GET | `/v1/words/:english` | Full word: senses, all translations, categories |
| GET | `/v1/words/:english/translations/:lang` | Translations only |
| GET | `/v1/random?level=B1&lang=fa&category=food` | Random word with full payload |
| GET | `/v1/categories` | All categories + word counts |
| GET | `/v1/categories/:slug/words?lang=fa&limit=100` | Words in a category |

CORS: `*`. Cache: `public, max-age=300, s-maxage=300`. No auth.

## Deploying (first-time setup)

```bash
cd api/
npm install
npx wrangler login            # one-time, opens browser
npx wrangler d1 create openjam
# Paste the returned database_id into wrangler.toml
python sqlite/build-data.py   # produces sqlite/data.sql from data/json/
npm run d1:schema             # applies sqlite/schema.sql to remote D1
npm run d1:import             # applies sqlite/data.sql to remote D1
npm run deploy                # ships the Worker
```

After the first deploy succeeds on `*.workers.dev`, add the custom domain:

```bash
npx wrangler deployments domains add openjam.amirj4m.com
```

(or via the Cloudflare dashboard: Workers & Pages -> openjam-api -> Settings -> Triggers -> Custom Domains)

## Updating the dataset

When `data/json/*.json` change (new Openjam release):

```bash
python sqlite/build-data.py
npm run d1:import           # idempotent: INSERT OR IGNORE
```

The Worker code does not need to change for data updates.

## Local development

```bash
npm run d1:schema:local
npm run d1:import:local
npm run dev                  # http://localhost:8787
```
