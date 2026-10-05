"""Render the OKF bundle: one Asset doc per image under a folder per
category, a catalog doc, a tag index, a how-to-search playbook, and the
indexes. Output is a pure function of the cache, so re-runs only touch what
changed and the bundle diffs cleanly in git."""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from . import __version__

GENERATED_MARK = "image-classify"  # value of `generator:` in every file we own

# The house OKF standard this bundle follows (github.com/xcspl/xyno-okf-guide)
# and its canonical entry contract for agents, checked verbatim by its validator.
HOUSE_VERSION = "0.3"
HANDLING = [
    "Read this index before opening any doc in this bundle.",
    "Navigate by index entries; never glob the tree.",
    "A write to any directory updates that directory's index.md in the same commit.",
    "Full rules live in okf-guide.md in the workspace registry (Playbook at its bundle root).",
]


def _fm(data: dict) -> str:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100).rstrip()
    return f"---\n{body}\n---\n"


def _one_line(s: str) -> str:
    return " ".join(str(s).split())


def _md_escape(s: str) -> str:
    return s.replace("[", "\\[").replace("]", "\\]")


def kebab(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:n].strip("-") or "image"


def apply_tag_map(tags: list[str], tag_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = tag_map.get(t, t)
        if t and t not in out:
            out.append(t)
    return out


class Entry:
    """One image doc to write: the cached analysis plus where its file(s) live."""

    def __init__(self, sha: str, rec: dict, files: list, tag_map: dict[str, str]):
        a = rec["analysis"]
        self.sha = sha
        self.rec = rec
        self.files = sorted(files, key=lambda f: f.rel)
        self.main = self.files[0]
        self.title = a["title"]
        self.description = a["description"] or a["title"]
        self.category = a["category"]
        self.tags = apply_tag_map(a["tags"], tag_map)
        self.slug = rec["slug"]
        self.path = f"{self.category}/{self.slug}.md"  # bundle-relative


def image_doc(e: Entry, bundle: Path, root: Path) -> str:
    a, f = e.rec["analysis"], e.main
    fm: dict = {
        "type": "Asset",
        "title": e.title,
        "description": e.description,
        "resource": f.rel,
        "tags": e.tags,
        "timestamp": e.rec["analyzed_at"],
        "status": "active",
        "category": e.category,
        "generator": GENERATED_MARK,
        "generated": {"by": f"image-classify {__version__} / {e.rec['model']}", "at": e.rec["analyzed_at"]},
        "file": {k: v for k, v in {
            "name": Path(f.rel).name,
            "folder": str(Path(f.rel).parent.as_posix()) if Path(f.rel).parent.as_posix() != "." else "",
            "sha256": e.sha,
            "bytes": f.size_bytes,
            "width": f.width,
            "height": f.height,
            "format": f.format,
            "copies": [c.rel for c in e.files[1:]] or None,
        }.items() if v not in (None, "")},
    }
    for k in ("taken_at", "camera", "gps"):
        v = getattr(f, k)
        if v:
            fm[k] = v
    fm["people_count"] = a["people_count"]
    fm["setting"] = a["setting"]
    if a["sensitive"]:
        fm["sensitive"] = True
        if a["sensitive_reason"]:
            fm["sensitive_reason"] = a["sensitive_reason"]

    doc_dir = bundle / e.category
    link = Path(os.path.relpath(root / f.rel, doc_dir)).as_posix()
    parts = [_fm(fm), f"# {e.title}\n", f"![{_md_escape(e.title)}](<{link}>)\n"]
    if a["details"]:
        parts.append(f"{a['details']}\n")
    if a["text_in_image"]:
        parts.append("# Text in image\n\n> " + a["text_in_image"].replace("\n", "\n> ") + "\n")
    loc = [f"- File: [`{f.rel}`](<{link}>)"]
    for c in e.files[1:]:
        loc.append(f"- Copy: `{c.rel}`")
    if f.width and f.height:
        loc.append(f"- {f.width}×{f.height} {f.format or ''}, {f.size_bytes:,} bytes".rstrip())
    if f.taken_at:
        loc.append(f"- Taken: {f.taken_at}" + (f" on {f.camera}" if f.camera else ""))
    if f.gps:
        loc.append(f"- GPS: {f.gps['lat']}, {f.gps['lon']}")
    parts.append("# File\n\n" + "\n".join(loc) + "\n")
    return "\n".join(parts)


def catalog_doc(entries: list[Entry], root: Path, unreadable: list, model: str) -> str:
    cats = Counter(e.category for e in entries)
    tags = Counter(t for e in entries for t in e.tags)
    sensitive = [e for e in entries if e.rec["analysis"]["sensitive"]]
    files = sum(len(e.files) for e in entries)
    latest = max((e.rec["analyzed_at"] for e in entries), default="")
    fm = {
        "type": "Asset",
        "title": f"Image catalog: {root.name}",
        "description": f"{len(entries)} distinct images ({files} files) under {root.name}/, described by a vision model and filed into {len(cats)} categories with {len(tags)} tags.",
        "resource": "../",
        "tags": ["image", "catalog", "photo-library"],
        "timestamp": latest,
        "status": "active",
        "generator": GENERATED_MARK,
        "generated": {"by": f"image-classify {__version__} / {model}", "at": latest},
    }
    rows = "\n".join(f"| [{c}](/{c}/index.md) | {n} |" for c, n in sorted(cats.items(), key=lambda x: (-x[1], x[0])))
    top = ", ".join(f"{t} ({n})" for t, n in tags.most_common(30))
    out = [_fm(fm), f"# Image catalog: {root.name}\n",
           "Every image under the catalogued directory (the parent of this bundle) has one doc here, "
           "filed by category. `resource:` in each doc is the image path relative to that directory. "
           "See [how to search](/how-to-search.md) and the [tag index](/tags.md).\n",
           "# Categories\n\n| Category | Images |\n|---|---|\n" + rows + "\n",
           f"# Most used tags\n\n{top}\n"]
    if sensitive:
        out.append(f"# Flagged sensitive\n\n{len(sensitive)} images were flagged by the model as sensitive "
                   "(ID documents, financial data, private messages ...): `grep -rl '^sensitive: true' okf/`.\n")
    if unreadable:
        out.append("# Not catalogued\n\nThese files could not be read or described:\n\n" +
                   "\n".join(f"- `{rel}` — {why}" for rel, why in sorted(unreadable)) + "\n")
    return "\n".join(out)


def tags_doc(entries: list[Entry], latest: str) -> str:
    by_tag: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        for t in e.tags:
            by_tag[t].append(e)
    fm = {
        "type": "Reference",
        "title": "Tag index",
        "description": f"Every tag used in this catalog ({len(by_tag)}), most used first, with links to the images that carry it.",
        "tags": ["tag-index", "image", "catalog"],
        "timestamp": latest,
        "status": "active",
        "generator": GENERATED_MARK,
    }
    out = [_fm(fm), "# Tag index\n", "Tags were normalised (lowercase, hyphenated) and synonyms merged across the library.\n"]
    for t, es in sorted(by_tag.items(), key=lambda x: (-len(x[1]), x[0])):
        lines = "\n".join(f"* [{_md_escape(e.title)}](/{e.path})" for e in sorted(es, key=lambda e: e.title.lower()))
        out.append(f"## {t} ({len(es)})\n\n{lines}\n")
    return "\n".join(out)


HOW_TO_SEARCH = """# How to search this catalog

This bundle is generated by [image-classify](https://github.com/xcspl/image-classify)
and is the bundle's local rules: browse it through the indexes (root →
category → image); to find images by content, use the searches below, which
read the docs' text rather than walking the tree. Re-run `image-classify index`
instead of editing generated docs — hand-written docs at the bundle root are
kept and listed in the root index.

Each image is one markdown doc at `<category>/<slug>.md` with YAML
frontmatter. Search the frontmatter and body text, then open the image at the
doc's `resource:` path, which is relative to the catalogued directory (the
parent of this bundle).

## Fields

| Field | Meaning |
|---|---|
| `title`, `description` | What the image shows, in words a search would use |
| `category` | One of the folder names in this bundle |
| `tags` | Lowercase, hyphenated, synonyms merged — the best search key |
| `resource` | Image path relative to the catalogued directory |
| `file.sha256` | Content hash (duplicates share one doc; see `file.copies`) |
| `taken_at`, `camera`, `gps` | From EXIF, when the file had them |
| `people_count`, `setting` | Counted / judged by the model |
| `sensitive` | Present and `true` when the model flagged private content |

Body sections: the longer description, `# Text in image` (visible text,
verbatim — search it for words on signs, documents, screenshots), `# File`.

## Recipes (run from the catalogued directory)

    # by tag (exact)
    grep -rlx --include='*.md' -- '- beach' okf/
    # by any word in title, description, details or visible text
    grep -rli --include='*.md' 'sunset' okf/
    # a category
    ls okf/receipt/
    # flagged images
    grep -rl '^sensitive: true' okf/
    # taken in a month
    grep -rl "^taken_at: '2024-12" okf/
    # ranked search, with filters
    image-classify search . sunset beach --category landscape --tag sea

Start from [the catalog](/catalog.md) for counts per category and the most
used tags, or the [tag index](/tags.md) for every tag with its images.
"""


def howto_doc(latest: str) -> str:
    fm = {
        "type": "Playbook",
        "title": "How to search this catalog",
        "description": "Where each field lives in the image docs and the grep and image-classify search recipes that find images by tag, words, visible text, category, date or sensitivity.",
        "tags": ["search", "image", "catalog", "agent"],
        "timestamp": latest,
        "status": "active",
        "generator": GENERATED_MARK,
    }
    return _fm(fm) + "\n" + HOW_TO_SEARCH


def _read_fm(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    try:
        v = yaml.safe_load(text[4:end])
        return v if isinstance(v, dict) else None
    except yaml.YAMLError:
        return None


def render(bundle: Path, root: Path, entries: list[Entry], unreadable: list, model: str) -> dict:
    """Write the bundle. Returns {"written": n, "removed": n}. Files we did
    not generate are never touched; hand-written docs at the bundle root are
    listed in the root index under # Notes."""
    latest = max((e.rec["analyzed_at"] for e in entries), default="")
    files: dict[str, str] = {}
    by_cat: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        files[e.path] = image_doc(e, bundle, root)
        by_cat[e.category].append(e)
    for cat, es in by_cat.items():
        lines = "\n".join(f"* [{_md_escape(e.title)}]({e.slug}.md) - {_one_line(e.description)}"
                          for e in sorted(es, key=lambda e: (e.title.lower(), e.slug)))
        files[f"{cat}/index.md"] = f"# {cat.capitalize()}\n\n{lines}\n"
    files["catalog.md"] = catalog_doc(entries, root, unreadable, model)
    files["tags.md"] = tags_doc(entries, latest)
    files["how-to-search.md"] = howto_doc(latest)

    notes = []
    if bundle.exists():
        for p in sorted(bundle.glob("*.md")):
            if p.name in files or p.name in ("index.md", "log.md"):
                continue
            fm = _read_fm(p)
            if fm and fm.get("generator") != GENERATED_MARK:
                notes.append(f"* [{fm.get('title', p.stem)}]({p.name}) - {_one_line(fm.get('description', ''))}")

    cat_lines = []
    for cat, es in sorted(by_cat.items(), key=lambda x: (-len(x[1]), x[0])):
        top = Counter(t for e in es for t in e.tags).most_common(6)
        cat_lines.append(f"* [{cat.capitalize()}]({cat}/index.md) - {len(es)} image{'s' if len(es) != 1 else ''}"
                         + (f"; tags: {', '.join(t for t, _ in top)}" if top else ""))
    idx = [
        _fm({"okf_version": "0.2", "house_version": HOUSE_VERSION, "bundle": bundle.name,
             "handling": HANDLING, "local_rules": "/how-to-search.md"}),
        "# Catalog\n",
        f"* [Image catalog: {root.name}](catalog.md) - {_one_line(_read_desc(files['catalog.md']))}",
        "* [How to search this catalog](how-to-search.md) - Field meanings and grep / image-classify search recipes.",
        f"* [Tag index](tags.md) - Every tag with the images that carry it.\n",
        "# Categories\n",
        "\n".join(cat_lines) + "\n",
    ]
    if notes:
        idx += ["# Notes\n", "\n".join(notes) + "\n"]
    files["index.md"] = "\n".join(idx)

    written = removed = 0
    for rel, content in files.items():
        p = bundle / rel
        if p.exists() and p.read_text(encoding="utf-8") == content:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)
        written += 1
    # remove our own docs that no longer correspond to an image, then the
    # index of any folder left with nothing else in it
    if bundle.exists():
        for p in sorted(bundle.rglob("*.md")):
            if p.relative_to(bundle).as_posix() in files or p.name == "index.md":
                continue
            fm = _read_fm(p)
            if fm and fm.get("generator") == GENERATED_MARK:
                p.unlink()
                removed += 1
        for p in sorted(bundle.rglob("index.md")):
            if p.parent != bundle and p.relative_to(bundle).as_posix() not in files \
                    and not any(q.suffix == ".md" and q.name != "index.md" for q in p.parent.iterdir()):
                p.unlink()
                removed += 1
        for d in sorted((d for d in bundle.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
            if not any(d.iterdir()):
                d.rmdir()
    return {"written": written, "removed": removed}


def _read_desc(doc: str) -> str:
    end = doc.find("\n---", 4)
    v = yaml.safe_load(doc[4:end]) if doc.startswith("---\n") and end != -1 else {}
    return (v or {}).get("description", "")
