---
type: Decision
title: "Design: how image-okf catalogs a library"
description: Why one OpenAI-compatible client, content-hash caching, a fixed category list plus merged tags, Asset docs per image, and a deterministic generated bundle.
tags: [image-okf, design, okf, vision-model, catalog]
timestamp: '2026-10-05'
status: active
sources:
  - resource: ../image_okf/pipeline.py
  - resource: ../image_okf/okfwrite.py
  - resource: ../image_okf/model.py
---

# Design: how image-okf catalogs a library

**The goal.** An agent should find an image by what is in it — "the receipt
from the Goa trip" — by reading a few small text files, never by opening
images. So the output is an OKF bundle of one doc per image, and every
choice below serves search by a later agent.

**One client, OpenAI-compatible chat.** MiniMax's vision models (M3,
M3.1-Flash) take images as `image_url` content parts on the OpenAI-compatible
endpoint, and so do OpenRouter, Ollama's `/v1`, and gateways like llmhub.
Plain `urllib`, no SDK: the provider is two settings, and a fully local run
(Ollama) needs no key. `<think>` blocks and code fences are stripped before
the JSON is read, because reasoning models wrap their answers.

**Cache by content hash, not by path.** `.image-okf/cache.json` beside the
images maps sha256 → analysis. A moved or renamed file is a path update with
no model call; duplicates share one doc (`file.copies`); a stopped run
resumes. The doc's slug (`<file-stem>-<sha6>`) is fixed at first analysis,
so concept ids survive renames.

**Two levels of classification.** One category from a fixed list (one folder
per category — the bundle's tiering) and free tags for everything finer.
Free tags drift between calls (`beaches`, `beach`), so after describing, one
text-only pass over the whole vocabulary asks the model to merge synonyms;
the merge map is cached and applied at render time, never written into the
analyses, so it can be redone.

**The bundle is generated and deterministic.** Rendered from the cache alone,
timestamps are analysis dates (not run dates), and files are rewritten only
when their content changes — an unchanged library rewrites nothing and diffs
cleanly in git. Every generated file carries `generator: image-okf`; cleanup
removes only those, so hand-written docs at the bundle root survive and are
listed under `# Notes`.

**Types.** Each image is an `Asset` (the house type for media), the catalog
overview another `Asset`, the tag index a `Reference`, and the search recipes
a `Playbook` named as the bundle's `local_rules`. Root index carries the
house 0.3 head (`house_version`, canonical `handling`), checked by the
house validator in the test suite.

**Privacy is visible, not hidden.** Hosted models receive every image
(downscaled to 1024 px). The CLI says so before a run, `--dry-run` lists
what would be sent, and the model flags private content (`sensitive: true`)
so a later agent can avoid it.
