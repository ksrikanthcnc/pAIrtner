"""Fact tag format helpers.

``facts.tag`` is TEXT. New rows store a JSON array (``'["kids", "school"]'``);
legacy rows store one plain string (``'kids'``). Everything reading a tag must
go through :func:`parse_fact_tags` so both formats work.
"""
from __future__ import annotations

import json
from typing import Any


def normalize_tags(raw: Any) -> list[str]:
    """Normalize LLM/user input (None, str, or list) to a lowercase unique list."""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[str] = []
    for it in items:
        if it is None:
            continue
        t = str(it).strip().lower()
        if t and t not in out:
            out.append(t)
    return out


def parse_fact_tags(stored: Any) -> list[str]:
    """Parse a stored tag value (JSON array, plain string, list, or None)."""
    if stored is None:
        return []
    if isinstance(stored, (list, tuple)):
        return normalize_tags(stored)
    s = str(stored).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return normalize_tags(s)
        if isinstance(data, list):
            return normalize_tags(data)
    return normalize_tags(s)


def encode_tags(raw: Any) -> str | None:
    """Encode tags for storage as a JSON array string; None when empty."""
    tags = normalize_tags(parse_fact_tags(raw) if isinstance(raw, str) else raw)
    return json.dumps(tags) if tags else None


def first_tag(stored: Any) -> str | None:
    tags = parse_fact_tags(stored)
    return tags[0] if tags else None
