# Verification (Claude's tools, not part of the project)

The original (reference commit, see `../PLAN.md`) and the rebuild (`src/` of this branch) run on the
same inputs; the rebuild must produce the same results as the ones recorded from the original.

```
uv venv -p 3.12.11 ../../venv            # outside the clone (see PLAN.md: environment)
VIRTUAL_ENV=../../venv uv pip install -r requirements.txt playwright==1.63.0
python run.py record [NAME ...]          # original -> refs/NAME.json   (setup only)
python run.py stable [NAME ...]          # original twice: repeatable?
python run.py check  [NAME ...]          # rebuild  -> compared with refs/NAME.json
```

`s04_real_model` needs the real model next to the clone: `../fixtures/model-it/model.zip` (GitHub
release `model-it-v1/opus-mt-en-it-ct2.zip`). `s13_editor_page` needs Playwright's Chromium.

## Pieces

- `harness/versions.py` — where each version's program comes from (`git archive` of the reference).
- `harness/addon.py` — fake Text Translator: lays out `addons/text_translator/modules/<folder>`,
  starts the module, speaks modulep v2, keeps a cache.db like the addon, records stderr/answers/files.
- `harness/inject/sitecustomize.py` — loaded by the module process: fake network (routes file,
  offline by default, request log) and an optional scripted ctranslate2/sentencepiece.
- `harness/github.py` — fake GitHub content: API commit, raw files, patch pieces, model assets.
- `scenarios/*.py` — level 2 (the whole module inside the fake addon); `functions/*.py` — level 3
  (functions called directly: the library API used by the private tools, Windows-only functions with
  a fake OS). Each prints/returns JSON; the runner compares it.

Inputs are synthetic: no bulk English game text is committed. Timing-dependent values are
normalized (durations, CPU, groups of answers, times), races that exist in the original too are
read again once settled (see the comments in s10, s12, s13).
