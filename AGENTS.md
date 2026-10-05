# AGENTS.md

OKF bundle: okf/ — read `okf/index.md` first.

## Working on this repo

- Python 3.10+, dependencies: Pillow, PyYAML. No SDKs: the model client is
  plain `urllib` against the OpenAI-compatible chat API (`image_okf/model.py`).
- Tests: `python3 -m unittest discover -s tests` — offline, a fake model
  server; one test runs the house validator if
  `~/local/xyno-okf-guide/okf-validate.py` (or `$OKF_VALIDATE`) exists.
- Layout: `scan.py` (files, hashes, EXIF; no model) → `model.py` (prompt,
  client, JSON parsing) → `pipeline.py` (cache, workers, tag merge) →
  `okfwrite.py` (the generated bundle) · `search.py` · `cli.py`.
- Generated bundles must pass the house OKF validator at the
  `house_version` in `okfwrite.HOUSE_VERSION`; bump both together.

## Using a catalog this tool generated

Read the catalog's `okf/index.md`, then its `how-to-search.md` — it lists
every field and the grep / `image-okf search` recipes. An image doc's
`resource:` is the image path relative to the catalogued directory.
