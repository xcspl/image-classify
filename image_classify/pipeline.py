"""The workflow: scan → describe new images with the vision model (cached by
content hash) → merge the tag vocabulary across the library → write the
bundle. Safe to stop and re-run at any point; only new or changed images
cost a model call."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import model as m
from .jsonwrite import render_json
from .okfwrite import Entry, kebab, render
from .scan import ImageFile, describe_file, find_images, prepare_for_model

CACHE_VERSION = 1


@dataclass
class Options:
    root: Path
    cfg: m.ModelConfig
    formats: tuple[str, ...] = ("okf", "json")  # any of okf, json
    bundle: Path | None = None  # default: ROOT/okf
    json_path: Path | None = None  # default: ROOT/image-classify.json
    categories: list[str] = field(default_factory=lambda: list(m.DEFAULT_CATEGORIES))
    workers: int = 4
    max_side: int = 1024
    limit: int | None = None
    dry_run: bool = False
    consolidate: bool = True
    reanalyze: bool = False
    log: callable = print


class Cache:
    """`.image-classify/cache.json` beside the images: analyses by sha256, the
    merged tag map, and which tags the merge has already seen."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        self.images: dict[str, dict] = data.get("images", {})
        self.tag_map: dict[str, str] = data.get("tag_map", {})
        self.tags_seen: set[str] = set(data.get("tags_seen", []))

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "version": CACHE_VERSION,
                "images": self.images,
                "tag_map": self.tag_map,
                "tags_seen": sorted(self.tags_seen),
            }, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)


def _slug(f: ImageFile, used: set[str]) -> str:
    base = f"{kebab(Path(f.rel).stem)}-{f.sha256[:6]}"
    s, i = base, 2
    while s in used:
        s, i = f"{base}-{i}", i + 1
    used.add(s)
    return s


def analyze_one(f: ImageFile, opts: Options) -> dict:
    jpeg = prepare_for_model(f.path, opts.max_side)
    context = {
        "file name": Path(f.rel).name,
        "folder": str(Path(f.rel).parent.as_posix()) if Path(f.rel).parent.as_posix() != "." else "",
        "taken": f.taken_at,
        "camera": f.camera,
        "size": f"{f.width}x{f.height}" if f.width else "",
    }
    last: Exception | None = None
    for _ in range(2):  # one retry on an unparsable reply
        text = m.chat(opts.cfg, m.analysis_messages(jpeg, opts.categories, context))
        try:
            return m.clean_analysis(m.extract_json(text), opts.categories)
        except ValueError as e:
            last = e
    raise m.ModelError(0, f"unparsable reply: {last}")


def consolidate_tags(cache: Cache, opts: Options, counts: Counter) -> int:
    """Ask the model once to merge synonyms when new tags appeared. Returns
    the number of new mappings."""
    new = [t for t in counts if t not in cache.tags_seen and t not in cache.tag_map]
    if not new or len(counts) < 2:
        cache.tags_seen.update(counts)
        return 0
    vocab = counts.most_common(1500)
    try:
        raw = m.extract_json(m.chat(opts.cfg, m.consolidation_messages(vocab), max_tokens=8000))
    except Exception as e:  # a failed merge never fails the run
        opts.log(f"  tag merge skipped: {e}")
        return 0
    added = 0
    for src, dst in raw.items():
        s, d = m.norm_tag(src), m.norm_tag(dst)
        if s and d and s != d and s in counts:
            cache.tag_map[s] = d
            added += 1
    # flatten chains (a→b, b→c ⇒ a→c) and drop cycles
    for s in list(cache.tag_map):
        seen, d = {s}, cache.tag_map[s]
        while d in cache.tag_map and d not in seen:
            seen.add(d)
            d = cache.tag_map[d]
        if d in seen:
            del cache.tag_map[s]
        else:
            cache.tag_map[s] = d
    cache.tags_seen.update(counts)
    return added


def run(opts: Options) -> dict:
    root, log = opts.root.resolve(), opts.log
    bundle = (opts.bundle or root / "okf").resolve()
    json_path = (opts.json_path or root / "image-classify.json").resolve()
    cache = Cache(root / ".image-classify" / "cache.json")
    t0 = time.time()

    paths = find_images(root, skip=[bundle])
    log(f"scanning {len(paths)} image files under {root}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(lambda p: describe_file(p, root), paths))

    unreadable = [(f.rel, f.error) for f in files if f.error]
    by_sha: dict[str, list[ImageFile]] = defaultdict(list)
    for f in files:
        if not f.error:
            by_sha[f.sha256].append(f)

    todo = [fs[0] for sha, fs in sorted(by_sha.items(), key=lambda x: x[1][0].rel)
            if opts.reanalyze or sha not in cache.images]
    if opts.limit is not None:
        todo = todo[: opts.limit]
    log(f"{len(by_sha)} distinct images, {len(by_sha) - len(todo) if not opts.reanalyze else 0} already catalogued, "
        f"{len(todo)} to describe with {opts.cfg.model} at {opts.cfg.base_url} ({opts.cfg.api_format})")
    if opts.dry_run:
        for f in todo:
            log(f"  would send {f.rel}")
        return {"files": len(files), "distinct": len(by_sha), "to_describe": len(todo), "dry_run": True}

    used = {r["slug"] for r in cache.images.values() if "slug" in r}
    failed: list[tuple[str, str]] = []
    done = 0
    today = date.today().isoformat()
    with ThreadPoolExecutor(max_workers=max(1, opts.workers)) as pool:
        futs = {pool.submit(analyze_one, f, opts): f for f in todo}
        for fut in as_completed(futs):
            f = futs[fut]
            try:
                analysis = fut.result()
            except Exception as e:
                failed.append((f.rel, str(e)[:200]))
                log(f"  ✗ {f.rel}: {str(e)[:160]}")
                continue
            with cache.lock:
                prev = cache.images.get(f.sha256, {})
                slug = prev.get("slug") or _slug(f, used)
                cache.images[f.sha256] = {"analysis": analysis, "model": opts.cfg.model,
                                          "analyzed_at": today, "slug": slug}
            done += 1
            log(f"  ✓ [{done}/{len(todo)}] {f.rel} → {analysis['category']}: {analysis['title']}")
            if done % 10 == 0:
                cache.save()
    cache.save()

    present = {sha: fs for sha, fs in by_sha.items() if sha in cache.images}
    counts = Counter(t for sha in present for t in cache.images[sha]["analysis"]["tags"])
    if opts.consolidate:
        n = consolidate_tags(cache, opts, counts)
        if n:
            log(f"merged {n} tag variants")
        cache.save()

    entries = [Entry(sha, cache.images[sha], fs, cache.tag_map) for sha, fs in present.items()]
    model_names = sorted({e.rec["model"] for e in entries}) or [opts.cfg.model]
    result = {
        "files": len(files), "distinct": len(by_sha), "catalogued": len(entries),
        "described_now": done, "failed": len(failed), "unreadable": len(unreadable),
    }
    if "okf" in opts.formats:
        stats = render(bundle, root, entries, unreadable + failed, ", ".join(model_names))
        result.update({"okf": str(bundle), "docs_written": stats["written"], "docs_removed": stats["removed"]})
    if "json" in opts.formats:
        changed = render_json(json_path, root, entries, unreadable + failed)
        result.update({"json": str(json_path), "json_written": changed})
    result["seconds"] = round(time.time() - t0, 1)
    return result


def stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
