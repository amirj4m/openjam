# Openjam Python package

Status: **Planned (v0.3.0)**

A pip-installable client for Openjam.

## Planned usage

```python
from openjam import Openjam

oj = Openjam()

word = oj.word("run")
print(word.translation("fa"))            # دویدن
print(word.senses[0].example_en)         # "She likes to run in the morning."

for w in oj.words(level="A1", lang="fa"):
    print(w.english, w.translation("fa"))
```

## Installation (when released)

```bash
pip install openjam
```

## Design principles

- Zero-config out of the box (ships with a bundled snapshot)
- Optional: point at the live REST API for the latest data
- Typed (`py.typed`) — full type hints for IDE support

Follow the main repository for progress.
