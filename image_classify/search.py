"""Ranked search over a catalog — the JSON file when there is one (a single
read), otherwise the OKF bundle. What an agent or a person runs to find
images by meaning without opening them."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

WORD = re.compile(r"[a-z0-9]+")
# field weights: a hit in the tags or title says more than one in the body
WEIGHTS = {"tags": 4.0, "title": 3.0, "category": 2.5, "description": 2.0, "body": 1.0, "path": 1.0}


@dataclass
class Hit:
    score: float
    path: str
    title: str
    category: str
    tags: list[str]
    doc: str | None  # the OKF doc, bundle-relative, when searching a bundle


def records_from_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data.get("images", []):
        yield {"path": r["path"], "title": r.get("title", ""), "description": r.get("description", ""),
               "category": r.get("category", ""), "tags": r.get("tags") or [], "sensitive": bool(r.get("sensitive")),
               "body": " ".join([r.get("details", ""), r.get("text_in_image", "")] + r.get("copies", [])), "doc": None}


def records_from_okf(bundle: Path):
    for p in sorted(bundle.rglob("*.md")):
        if p.name in ("index.md", "log.md"):
            continue
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---", 4)
        try:
            fm = yaml.safe_load(text[4:end]) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict) or "file" not in fm or "resource" not in fm:
            continue  # only image docs
        yield {"path": str(fm["resource"]), "title": str(fm.get("title", "")), "description": str(fm.get("description", "")),
               "category": str(fm.get("category", "")), "tags": [str(t) for t in fm.get("tags") or []],
               "sensitive": bool(fm.get("sensitive")), "body": text[end + 4:], "doc": p.relative_to(bundle).as_posix()}


def _words(s: str) -> set[str]:
    return set(WORD.findall(s.lower()))


def search(records, query: str, *, tags: list[str] = (), category: str | None = None,
           sensitive: bool | None = None, limit: int = 20) -> list[Hit]:
    q = WORD.findall(query.lower())
    hits: list[Hit] = []
    for r in records:
        if tags and not all(t in r["tags"] for t in tags):
            continue
        if category and r["category"] != category:
            continue
        if sensitive is not None and r["sensitive"] != sensitive:
            continue
        fields = {
            "tags": set(w for t in r["tags"] for w in [t, *t.split("-")]),
            "title": _words(r["title"]),
            "category": {r["category"]},
            "description": _words(r["description"]),
            "body": _words(r["body"]),
            "path": _words(r["path"]),
        }
        score = 0.0
        for w in q:
            matched = [WEIGHTS[k] for k, ws in fields.items() if w in ws]
            if not matched:  # prefix match on tags/title for stems (sunsets → sunset)
                matched = [WEIGHTS[k] * 0.5 for k in ("tags", "title")
                           if any(x.startswith(w) or w.startswith(x) for x in fields[k] if len(x) > 3)]
            score += sum(matched)
        if q and score == 0:
            continue
        hits.append(Hit(score, r["path"], r["title"], r["category"], r["tags"], r["doc"]))
    hits.sort(key=lambda h: (-h.score, h.path))
    return hits[:limit]
