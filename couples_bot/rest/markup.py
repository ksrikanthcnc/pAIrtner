"""Safe conversion of bot text (Telegram-style HTML) for web clients.

``to_markup`` keeps only ``b i u code pre`` (no attributes) and escapes
everything else. ``to_plain`` strips tags and decodes entities.
"""
from __future__ import annotations

import html
import re

ALLOWED_TAGS = frozenset({"b", "i", "u", "code", "pre"})

# A candidate tag: <tag ...> or </tag>. Anything not matching stays literal text.
_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)(?:\s[^<>]*)?>")
_ANY_TAG_RE = re.compile(r"<[^<>]*>")


def _esc(text: str) -> str:
    """Escape &, <, > only (quotes are safe in text nodes)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_markup(text: str) -> str:
    """Return *text* with only whitelisted tags kept; all else entity-escaped.

    Existing entities in the input (``&lt;``, ``&amp;``...) are decoded first
    inside text runs and re-escaped, so the output has exactly one level of
    escaping and round-trips through ``to_plain``.
    """
    out: list[str] = []
    stack: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        name = m.group(2).lower()
        if name not in ALLOWED_TAGS:
            continue  # left in the text run, escaped below
        out.append(_esc(html.unescape(text[pos:m.start()])))
        pos = m.end()
        if m.group(1):  # closing tag
            if name in stack:
                while stack:
                    top = stack.pop()
                    out.append(f"</{top}>")
                    if top == name:
                        break
            # stray closing tag with no opener: dropped
        else:
            stack.append(name)
            out.append(f"<{name}>")
    out.append(_esc(html.unescape(text[pos:])))
    while stack:
        out.append(f"</{stack.pop()}>")
    return "".join(out)


def to_plain(text: str) -> str:
    """Strip whitelisted tags and decode entities; other markup stays as text."""
    def _drop(m: re.Match[str]) -> str:
        return "" if m.group(2).lower() in ALLOWED_TAGS else m.group(0)

    return html.unescape(_TAG_RE.sub(_drop, text))
