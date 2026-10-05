"""The model side: one OpenAI-compatible chat-completions client (MiniMax,
OpenRouter, an llmhub proxy, Ollama's /v1 ...), the per-image prompt, and a
tolerant JSON reader for what comes back."""

from __future__ import annotations

import base64
import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_MODEL = "MiniMax-M3"

# The fixed first level of the taxonomy. One folder per category in the
# bundle; tags carry everything finer. Override with --categories.
DEFAULT_CATEGORIES = [
    "people", "event", "nature", "landscape", "animal", "food", "architecture",
    "interior", "vehicle", "product", "document", "screenshot", "receipt",
    "diagram", "map", "artwork", "graphic", "other",
]


class ModelError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class ModelConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = 120.0
    retries: int = 4
    max_tokens: int = 4000  # room for reasoning models to think before the JSON
    api_format: str = "openai"  # or "anthropic" (Messages API: Anthropic, MiniMax, llmagento's /anthropic/v1)


def guess_format(base_url: str) -> str:
    return "anthropic" if "anthropic" in base_url.lower() else "openai"


def _to_anthropic(messages: list[dict]) -> list[dict]:
    """OpenAI-style messages (text and data-URI image_url parts) as Messages API content blocks."""
    out = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
            continue
        blocks = []
        for part in content:
            if part.get("type") == "image_url":
                url = part["image_url"]["url"]
                head, data = url.split(",", 1)
                media = head[len("data:"):].split(";")[0] or "image/jpeg"
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
            elif part.get("type") == "text":
                blocks.append({"type": "text", "text": part["text"]})
        out.append({"role": msg["role"], "content": blocks})
    return out


def chat(cfg: ModelConfig, messages: list[dict], *, max_tokens: int | None = None) -> str:
    """One non-streamed completion; returns the visible text. Retries
    429/5xx/network errors with jittered backoff, honouring Retry-After."""
    anthropic = cfg.api_format == "anthropic"
    url = cfg.base_url.rstrip("/") + ("/messages" if anthropic else "/chat/completions")
    body = json.dumps({
        "model": cfg.model,
        "messages": _to_anthropic(messages) if anthropic else messages,
        "max_tokens": max_tokens or cfg.max_tokens,
        "temperature": 0.2,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if anthropic:
        headers["anthropic-version"] = "2023-06-01"
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
        if anthropic:
            headers["x-api-key"] = cfg.api_key
    last: Exception | None = None
    for attempt in range(cfg.retries + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
                data = json.loads(resp.read())
            if anthropic:  # thinking blocks are skipped; only text blocks are the answer
                if data.get("type") == "error":
                    raise ModelError(500, f"provider error: {data.get('error')}")
                return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            if isinstance(text, list):  # some providers return content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            # MiniMax also reports some failures inside a 200
            base = data.get("base_resp") or {}
            if not text and base.get("status_code"):
                raise ModelError(500, f"provider error {base.get('status_code')}: {base.get('status_msg')}")
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = ModelError(e.code, f"HTTP {e.code}: {detail}")
            if e.code not in (408, 409, 429) and e.code < 500:
                raise last
            wait = _retry_after(e.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = ModelError(0, f"network: {e}")
            wait = None
        except ModelError as e:
            last = e
            wait = None
        if attempt < cfg.retries:
            time.sleep(wait if wait is not None else min(30.0, 2 * 2 ** attempt) * (0.75 + random.random() / 2))
    assert last is not None
    raise last


def _retry_after(v: str | None) -> float | None:
    try:
        return min(60.0, float(v)) if v else None
    except ValueError:
        return None


def image_prompt(categories: list[str], context: dict) -> str:
    hints = "\n".join(f"- {k}: {v}" for k, v in context.items() if v)
    return f"""Catalog this image for a searchable library. Another agent will later find it by searching your words, so name concrete, searchable things.

File context (use it as a hint, trust the pixels):
{hints}

Reply with ONE JSON object and nothing else:
{{
  "title": "specific title, at most 8 words",
  "description": "one sentence saying what the image shows",
  "details": "2-4 sentences: subjects, setting, notable objects, actions, mood, style",
  "category": "exactly one of: {', '.join(categories)}",
  "tags": ["8-15 lowercase tags: subjects, objects, place type, activity, season, style, colours; singular nouns; hyphenate multi-word tags"],
  "people_count": 0,
  "text_in_image": "visible text copied verbatim, or empty",
  "setting": "indoor | outdoor | digital | unknown",
  "sensitive": false,
  "sensitive_reason": "if sensitive is true: why (ID document, financial data, private message, medical, explicit), else empty"
}}

Rules: do not guess names of people, exact places or brands you cannot read or clearly recognise; say what you see instead."""


def analysis_messages(jpeg: bytes, categories: list[str], context: dict) -> list[dict]:
    b64 = base64.b64encode(jpeg).decode()
    return [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": image_prompt(categories, context)},
        ],
    }]


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_json(text: str) -> dict:
    """The first JSON object in a reply, ignoring <think> blocks and fences."""
    text = _THINK.sub("", text)
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(text[start:i + 1])
                        if isinstance(v, dict):
                            return v
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in the model reply")


_TAG_BAD = re.compile(r"[^a-z0-9-]+")


def norm_tag(t: str) -> str:
    t = str(t).strip().lower().replace("_", " ").replace("/", " ")
    t = re.sub(r"\s+", "-", t)
    t = _TAG_BAD.sub("", t)
    return re.sub(r"-{2,}", "-", t).strip("-")[:40]


def clean_analysis(raw: dict, categories: list[str]) -> dict:
    """Coerce a model reply into the fields we write; never trust its shape."""
    def s(k, n):
        v = raw.get(k)
        return " ".join(str(v).split())[:n] if v not in (None, "") else ""

    cat = norm_tag(raw.get("category", ""))
    tags: list[str] = []
    for t in raw.get("tags") or []:
        nt = norm_tag(t)
        if nt and nt not in tags:
            tags.append(nt)
    try:
        people = max(0, int(raw.get("people_count") or 0))
    except (TypeError, ValueError):
        people = 0
    setting = s("setting", 20).lower()
    return {
        "title": s("title", 90) or "Untitled image",
        "description": s("description", 300),
        "details": s("details", 1200),
        "category": cat if cat in categories else "other",
        "tags": tags[:20],
        "people_count": people,
        "text_in_image": " ".join(str(raw.get("text_in_image") or "").split())[:2000],
        "setting": setting if setting in ("indoor", "outdoor", "digital") else "unknown",
        "sensitive": raw.get("sensitive") is True or str(raw.get("sensitive")).strip().lower() in ("true", "yes", "1"),
        "sensitive_reason": s("sensitive_reason", 200),
    }


def consolidation_messages(vocab: list[tuple[str, int]]) -> list[dict]:
    lines = "\n".join(f"{t} ({n})" for t, n in vocab)
    return [{
        "role": "user",
        "content": f"""These tags (with usage counts) were written independently for images in one library. Merge tags that mean the same thing so search finds everything under one name: plurals to singular, synonyms and spelling variants to the most common or clearest form. Do not merge tags that are merely related (dog and puppy stay separate; car and automobile merge).

Reply with ONE JSON object mapping each tag that should change to its replacement, and nothing else. Omit tags that stay. Example: {{"cars": "car", "automobile": "car", "sea-shore": "seashore"}}

Tags:
{lines}""",
    }]
