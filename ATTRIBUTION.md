# Attribution

Openjam is MIT-licensed. To keep that promise, every external source we incorporate is openly licensed and compatible with MIT redistribution. This file tracks what's used in each release.

## v0.1.0 (planned)

| Source | Used for | License | Notes |
|---|---|---|---|
| [Princeton WordNet 3.1](https://wordnet.princeton.edu/) | Word list, senses, part of speech | WordNet License (BSD-style, MIT-compatible) | Copyright (c) 2011 The Trustees of Princeton University |
| Jamlex seed words (`fa` translations) | Initial Persian translation set | Original work, MIT | Migrated from the Jamlex project, same author |
| AI-generated translations (Claude / GPT) | Filling translation gaps | Output is original work, MIT | Each batch is reviewed by a native speaker before inclusion |

## Future releases

- Frequency ranks: source TBD (candidates: SUBTLEX-US under CC-BY-NC — not MIT-compatible, so will be re-derived; or use Google Books n-grams which are public)
- CEFR levels: cross-referenced from multiple public sources; only the resulting facts are stored
- Audio pronunciation: TBD (Wiktionary audio is CC-BY-SA — incompatible — so we'll generate or source separately)

## How we keep MIT clean

- We never import the *text* of definitions from copyrighted dictionaries (Oxford, Cambridge, Merriam-Webster, ...).
- We never bulk-import from CC-BY-SA sources like Wiktionary; that would force the whole dataset to inherit CC-BY-SA.
- AI-generated content is treated as original work of the contributor; humans review it before merge.
- If you spot a license issue, please open an issue immediately.
