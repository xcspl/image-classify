# image-classify

Point it at a folder of images. A vision model looks at every one and says
what's in it: a title, a description, one category, a set of tags, any text
it can read. You get that back as a catalog — a folder of small markdown files
(one per image, filed by category, cross-indexed by tag), a single JSON file,
or both (`--format okf|json|both`, default both).

The markdown folder is an [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundle (Open Knowledge Format), following the
[house OKF standard](https://github.com/xcspl/xyno-okf-guide). The point is
that an agent can later answer "find the photo of the receipt from the Goa
trip" by reading a few small text files — without opening a single image.

```
photos/                        image-classify.json     ← every image as one JSON record
                               okf/
├── trips/goa/IMG_2231.jpg     ├── index.md            ← start here
├── docs/scan-0042.png         ├── catalog.md          ← counts, top tags, flagged images
└── dog.webp                   ├── tags.md             ← every tag → its images
                               ├── how-to-search.md    ← recipes for agents
                               ├── landscape/
                               │   ├── index.md
                               │   └── img-2231-364ad3.md
                               ├── receipt/…
                               └── animal/…
```

Each image doc in the OKF bundle looks like this:

```markdown
---
type: Asset
title: Sunset over a sandy beach
description: An orange sunset over the sea and a sandy beach.
resource: trips/goa/IMG_2231.jpg
tags: [sunset, beach, sea, orange-sky, wave]
timestamp: '2026-10-05'
status: active
category: landscape
file: {name: IMG_2231.jpg, folder: trips/goa, sha256: 364a…, width: 4032, height: 3024}
taken_at: '2024-12-24T18:05:00'
camera: Canon EOS R6
people_count: 0
setting: outdoor
---

# Sunset over a sandy beach

![Sunset over a sandy beach](<../../trips/goa/IMG_2231.jpg>)

Waves roll in under an orange sky …
```

The JSON holds the same thing per image (`path`, `copies`, `title`,
`description`, `details`, `category`, `tags`, `text_in_image`,
`people_count`, `setting`, `sensitive`, `file`, `taken_at`, `camera`, `gps`,
`model`, `analyzed_at`), plus `counts` (images, files, per category, per tag)
and `not_catalogued`. Programs read the JSON; agents navigate the bundle.

## Install

Python 3.10+.

```bash
pipx install git+https://github.com/xcspl/image-classify      # or: pip install -e . from a clone
pipx inject image-classify pillow-heif                          # optional: iPhone .heic photos
```

## Run

```bash
export IMAGE_CLASSIFY_API_KEY=…          # a MiniMax key by default
image-classify index ~/Pictures/trip --dry-run    # what would be sent; costs nothing
image-classify index ~/Pictures/trip --limit 20   # try 20 first and read the docs
image-classify index ~/Pictures/trip              # the rest
```

Re-run it whenever the folder changes. Images are tracked by content hash:
new ones get described, moved or renamed ones just get their path updated,
deleted ones lose their doc. Nothing already described is sent again. Stop
it any time; it picks up where it left off.

## Any vision model

It speaks both common wire formats — OpenAI-style chat completions and the
Anthropic-style Messages API (`--api-format`, guessed from the URL) — so the
model is a setting:

| Provider | `--base-url` | `--model` |
|---|---|---|
| MiniMax (default) | `https://api.minimax.io/v1` | `MiniMax-M3`, `MiniMax-M3.1-Flash-Preview` |
| MiniMax, Messages API | `https://api.minimax.io/anthropic/v1` | same |
| [llmagento](https://llmagento.com) proxy | `https://llmagento.com/v1` or `https://llmagento.com/anthropic/v1` | your alias for a vision model; the key is a proxy token |
| OpenRouter | `https://openrouter.ai/api/v1` | any model with image input |
| Ollama, fully local | `http://localhost:11434/v1` | e.g. `qwen2.5vl:7b` (no key needed) |
| Any OpenAI- or Anthropic-compatible gateway | its base URL | its model name |

The key comes from `IMAGE_CLASSIFY_API_KEY` (or `MINIMAX_API_KEY` for MiniMax,
or any variable you name with `--api-key-env`). `IMAGE_CLASSIFY_BASE_URL`,
`IMAGE_CLASSIFY_MODEL` and `IMAGE_CLASSIFY_API_FORMAT` set the defaults.

**Privacy:** with a hosted model, every image is uploaded (downscaled to
1024 px on the long side; `--max-side` changes it). For a private library,
use a local model. Images that look private — IDs, bank statements,
messages — are flagged `sensitive: true` so an agent can steer clear of them.

## What the agent does

1. **Scan.** Hash every image, read EXIF (capture time, camera, GPS).
   No model involved.
2. **Describe.** Per image, the model gets a downscaled JPEG plus the file
   name and folder as hints, and returns a title, one-line description,
   details, one category, 8–15 tags, people count, any visible text
   (verbatim), and a sensitivity flag. Parallel (`--workers`), retried on
   rate limits.
3. **Merge tags.** Tags written image by image drift: `beaches`, `beach`,
   `sea-shore`. One pass over the whole vocabulary asks the model which
   tags mean the same thing and merges them, so a search for `beach` finds
   them all. Skip with `--no-consolidate`.
4. **Write the catalog** — the OKF bundle, the JSON, or both. Deterministic:
   an unchanged library rewrites nothing, so either output diffs cleanly in
   git. `--okf-out` / `--json-out` move them elsewhere.

Categories default to a fixed list (people, event, nature, landscape,
animal, food, architecture, interior, vehicle, product, document,
screenshot, receipt, diagram, map, artwork, graphic, other); replace it with
`--categories a,b,c`.

## Search

```bash
image-classify search ~/Pictures/trip sunset beach
image-classify search ~/Pictures/trip --tag receipt --json      # reads image-classify.json, else the bundle
grep -rlx -- '- beach' ~/Pictures/trip/okf/                 # by tag
grep -rli 'fresh mart' ~/Pictures/trip/okf/                 # words, including text seen in the image
```

The bundle's own `how-to-search.md` has the full set of recipes; point
your agent at `okf/index.md` and it finds its way from there.

## Notes

- The bundle is generated. Don't edit the image docs; re-run instead.
  Your own notes are safe: any doc you put at the bundle root is kept and
  listed in the root index.
- The cache lives in `.image-classify/` beside the images. Delete it to start over.
- Tests run offline against a fake model: `python3 -m unittest discover -s tests`.

## License

MIT
