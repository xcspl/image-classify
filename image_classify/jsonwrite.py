"""The JSON output: one file holding every image record, the category and
tag counts, and what could not be catalogued. Same source and the same
determinism as the OKF bundle — an unchanged library rewrites nothing."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import __version__

SCHEMA = 1


def record(e) -> dict:
    """One image as a flat, self-describing dict (an `Entry` from okfwrite)."""
    a, f = e.rec["analysis"], e.main
    r = {
        "id": e.sha,
        "path": f.rel,
        "copies": [c.rel for c in e.files[1:]],
        "title": e.title,
        "description": e.description,
        "details": a["details"],
        "category": e.category,
        "tags": e.tags,
        "text_in_image": a["text_in_image"],
        "people_count": a["people_count"],
        "setting": a["setting"],
        "sensitive": a["sensitive"],
        "sensitive_reason": a["sensitive_reason"],
        "file": {"name": Path(f.rel).name, "bytes": f.size_bytes, "width": f.width, "height": f.height, "format": f.format},
        "taken_at": f.taken_at,
        "camera": f.camera,
        "gps": f.gps,
        "model": e.rec["model"],
        "analyzed_at": e.rec["analyzed_at"],
    }
    return r


def render_json(path: Path, root: Path, entries: list, not_catalogued: list) -> bool:
    """Write the catalog JSON; returns True when the file changed."""
    recs = sorted((record(e) for e in entries), key=lambda r: r["path"])
    doc = {
        "generator": f"image-classify {__version__}",
        "schema": SCHEMA,
        "root": root.name,
        "updated": max((r["analyzed_at"] for r in recs), default=None),
        "counts": {
            "images": len(recs),
            "files": sum(1 + len(r["copies"]) for r in recs),
            "categories": dict(sorted(Counter(r["category"] for r in recs).items(), key=lambda x: (-x[1], x[0]))),
            "tags": dict(sorted(Counter(t for r in recs for t in r["tags"]).items(), key=lambda x: (-x[1], x[0]))),
            "sensitive": sum(1 for r in recs if r["sensitive"]),
        },
        "images": recs,
        "not_catalogued": [{"path": p, "reason": why} for p, why in sorted(not_catalogued)],
    }
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True
