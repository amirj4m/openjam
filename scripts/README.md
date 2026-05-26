# Openjam generators

Reproducible scripts that build the dataset from open sources.

## generate_seed.py

Builds the seed dataset end-to-end:

1. Pulls the top-N most-frequent English words from [`wordfreq`](https://pypi.org/project/wordfreq/) (modern corpus, MIT-licensed code).
2. Filters via Brown corpus POS tags to drop functional words and limit WordNet senses to ones matching the word's actual usage (no chemistry/geography junk for short words like `be`, `as`).
3. Lemmatizes (so `was`/`were`/`been` collapse to `be`).
4. Enriches each word with senses, parts of speech, definitions, and examples from [Princeton WordNet 3.1](https://wordnet.princeton.edu/) (BSD-style, MIT-compatible).
5. One Claude API call per word generates:
   - Persian (`fa`) translation + example per sense
   - 1-3 categories from a fixed 35-slug taxonomy
6. Resumable: existing translations and category assignments are loaded from `data/json/*.json` and reused — no wasted API calls on re-runs.
7. Emits all four pairs of `data/json/*.json` + `data/sql/*.sql`.

### Setup

```bash
python -m pip install -r scripts/requirements.txt
```

On first run, NLTK downloads WordNet automatically.

### Run

```bash
# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."   # bash
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # PowerShell

# Full seed (1000 words, ~15-30 min, ~$1-2 in API cost)
python scripts/generate_seed.py

# Quick smoke test (10 words, ~30 sec)
N_WORDS=10 python scripts/generate_seed.py

# Build words_en.sql without calling the API
SKIP_TRANSLATE=1 python scripts/generate_seed.py
```

### Reproducibility

Word and sense UUIDs are deterministic (uuid5 over a fixed namespace). Re-running produces the same IDs, so SQL inserts use `ON CONFLICT DO NOTHING` and are safe to apply incrementally.

### Cost

| Model | 1000 words | Notes |
|---|---|---|
| `claude-haiku-4-5-20251001` (default) | ~$1–2 | Recommended for translation |
| `claude-sonnet-4-6` | ~$5–10 | Higher quality, slower |

### Failure handling

Per-word translation failures are logged at the end and do not abort the run. Re-run with the same seed to fill gaps (existing translations are preserved on conflict).
