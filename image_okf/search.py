"""Ranked search over a generated bundle — what an agent (or a person)
runs to find images by meaning, without reading every doc."""

from __future__ import annotations

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
    resource: str
    title: str
    category: str
    doc: str
    tags: list[str]


def load_docs(bundle: Path):
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
        yield p, fm, text[end + 4:]


def _words(s: str) -> set[str]:
    return set(WORD.findall(s.lower()))


def search(bundle: Path, query: str, *, tags: list[str] = (), category: str | None = None,
           sensitive: bool | None = None, limit: int = 20) -> list[Hit]:
    q = [w for w in WORD.findall(query.lower())]
    hits: list[Hit] = []
    for p, fm, body in load_docs(bundle):
        doc_tags = [str(t) for t in fm.get("tags") or []]
        if tags and not all(t in doc_tags for t in tags):
            continue
        if category and fm.get("category") != category:
            continue
        if sensitive is not None and bool(fm.get("sensitive")) != sensitive:
            continue
        fields = {
            "tags": set(w for t in doc_tags for w in [t, *t.split("-")]),
            "title": _words(str(fm.get("title", ""))),
            "category": {str(fm.get("category", ""))},
            "description": _words(str(fm.get("description", ""))),
            "body": _words(body),
            "path": _words(str(fm.get("resource", ""))),
        }
        score = 0.0
        for w in q:
            matched = [WEIGHTS[k] for k, ws in fields.items() if w in ws]
            # prefix match on tags/title for stems (sunsets → sunset)
            if not matched:
                matched = [WEIGHTS[k] * 0.5 for k in ("tags", "title") if any(x.startswith(w) or w.startswith(x) for x in fields[k] if len(x) > 3)]
            score += sum(matched)
        if q and score == 0:
            continue
        hits.append(Hit(score, str(fm["resource"]), str(fm.get("title", "")), str(fm.get("category", "")),
                        p.relative_to(bundle).as_posix(), doc_tags))
    hits.sort(key=lambda h: (-h.score, h.resource))
    return hits[:limit]
