"""image-classify command line: `index` builds or refreshes a catalog, `search`
queries one."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from . import model as m
from .pipeline import Options, run, stderr_log
from .search import records_from_json, records_from_okf, search

FORMATS = {"okf": ("okf",), "json": ("json",), "both": ("okf", "json")}


def _cfg(a) -> m.ModelConfig:
    base = a.base_url or os.environ.get("IMAGE_CLASSIFY_BASE_URL") or m.DEFAULT_BASE_URL
    key = os.environ.get(a.api_key_env) if a.api_key_env else None
    key = key or os.environ.get("IMAGE_CLASSIFY_API_KEY") or (os.environ.get("MINIMAX_API_KEY") if "minimax" in base else None) or ""
    fmt = a.api_format or os.environ.get("IMAGE_CLASSIFY_API_FORMAT") or m.guess_format(base)
    return m.ModelConfig(base_url=base, api_key=key, model=a.model or os.environ.get("IMAGE_CLASSIFY_MODEL") or m.DEFAULT_MODEL,
                         timeout=a.timeout, api_format=fmt)


def cmd_index(a) -> int:
    root = Path(a.dir).expanduser().resolve()
    if not root.is_dir():
        stderr_log(f"not a directory: {root}")
        return 2
    cfg = _cfg(a)
    local = cfg.base_url.startswith(("http://localhost", "http://127.", "http://[::1]"))
    if not cfg.api_key and not local and not a.dry_run:
        stderr_log("no API key: set IMAGE_CLASSIFY_API_KEY (or MINIMAX_API_KEY), or --api-key-env NAME")
        return 2
    cats = [m.norm_tag(c) for c in a.categories.split(",")] if a.categories else list(m.DEFAULT_CATEGORIES)
    if "other" not in cats:
        cats.append("other")
    opts = Options(root=root, cfg=cfg, formats=FORMATS[a.format],
                   bundle=Path(a.okf_out).expanduser().resolve() if a.okf_out else None,
                   json_path=Path(a.json_out).expanduser().resolve() if a.json_out else None,
                   categories=cats, workers=a.workers, max_side=a.max_side, limit=a.limit, dry_run=a.dry_run,
                   consolidate=not a.no_consolidate, reanalyze=a.reanalyze, log=stderr_log)
    if not local and not a.dry_run:
        stderr_log(f"note: images are sent (downscaled to {a.max_side}px) to {cfg.base_url} — use a local model for private libraries")
    res = run(opts)
    print(json.dumps(res))
    return 1 if res.get("failed") else 0


def cmd_search(a) -> int:
    root = Path(a.dir).expanduser().resolve()
    json_path = Path(a.json_in).expanduser().resolve() if a.json_in else root / "image-classify.json"
    bundle = Path(a.okf_in).expanduser().resolve() if a.okf_in else root / "okf"
    if json_path.exists() and not a.okf_in:
        records = records_from_json(json_path)
    elif (bundle / "index.md").exists():
        records = records_from_okf(bundle)
    else:
        stderr_log(f"no catalog in {root} — run `image-classify index {a.dir}` first")
        return 2
    sens = True if a.sensitive else (False if a.not_sensitive else None)
    hits = search(records, " ".join(a.query), tags=a.tag or [], category=a.category, sensitive=sens, limit=a.limit)
    if a.json:
        print(json.dumps([h.__dict__ for h in hits], indent=1))
    else:
        for h in hits:
            print(f"{h.score:5.1f}  {h.path}  —  {h.title}  [{h.category}]" + (f"  ({bundle.name}/{h.doc})" if h.doc else ""))
    return 0 if hits else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="image-classify", description="Classify a directory of images with a vision model into a searchable catalog (OKF bundle, JSON, or both).")
    p.add_argument("--version", action="version", version=f"image-classify {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("index", help="describe new images and (re)write the catalog")
    i.add_argument("dir", help="image directory to catalog")
    i.add_argument("--format", choices=list(FORMATS), default="both", help="output: okf, json or both (default both)")
    i.add_argument("--okf-out", help="OKF bundle directory (default: DIR/okf)")
    i.add_argument("--json-out", help="JSON catalog file (default: DIR/image-classify.json)")
    i.add_argument("--model", help=f"model id (default: $IMAGE_CLASSIFY_MODEL or {m.DEFAULT_MODEL})")
    i.add_argument("--base-url", help=f"API base URL, OpenAI- or Anthropic-compatible (default: $IMAGE_CLASSIFY_BASE_URL or {m.DEFAULT_BASE_URL})")
    i.add_argument("--api-format", choices=["openai", "anthropic"],
                   help="wire format: openai (/chat/completions) or anthropic (/messages); default: guessed from the URL")
    i.add_argument("--api-key-env", help="name of the env var holding the key (default: IMAGE_CLASSIFY_API_KEY, then MINIMAX_API_KEY)")
    i.add_argument("--workers", type=int, default=4, help="parallel model calls (default 4)")
    i.add_argument("--max-side", type=int, default=1024, help="long edge sent to the model, px (default 1024)")
    i.add_argument("--limit", type=int, help="describe at most N new images this run")
    i.add_argument("--categories", help="comma-separated category list (default: built-in taxonomy)")
    i.add_argument("--timeout", type=float, default=120.0, help="seconds per model call")
    i.add_argument("--dry-run", action="store_true", help="list what would be sent; call nothing, write nothing")
    i.add_argument("--no-consolidate", action="store_true", help="skip the tag-merge pass")
    i.add_argument("--reanalyze", action="store_true", help="describe every image again, ignoring the cache")
    i.set_defaults(fn=cmd_index)

    s = sub.add_parser("search", help="ranked search over a catalog")
    s.add_argument("dir", help="the catalogued directory")
    s.add_argument("query", nargs="*", help="words to look for")
    s.add_argument("--json-in", help="JSON catalog to search (default: DIR/image-classify.json, used when present)")
    s.add_argument("--okf-in", help="OKF bundle to search instead (default: DIR/okf, used when there is no JSON)")
    s.add_argument("--tag", action="append", help="require this tag (repeatable)")
    s.add_argument("--category", help="only this category")
    s.add_argument("--sensitive", action="store_true", help="only images flagged sensitive")
    s.add_argument("--not-sensitive", action="store_true", help="exclude images flagged sensitive")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(fn=cmd_search)

    a = p.parse_args(argv)
    return a.fn(a)
