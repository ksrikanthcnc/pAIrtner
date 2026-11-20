"""Tiny HTML helpers: escaping, layout, tables, inline-SVG bar charts."""
from __future__ import annotations

import html
from typing import Any, Iterable, Sequence

from pairtner.dashboard.styles import CSS

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; manifest-src 'self'; "
        "worker-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}

MAX_CELL_CHARS = 400

_CSS = CSS

NAV = [
    ("/", "Overview"), ("/usage", "Usage"), ("/users", "Users"),
    ("/couples", "Couples"), ("/queue", "Queue"), ("/db", "Explorer"),
    ("/admin", "Admin"),
]


class Html(str):
    """Marker for already-escaped HTML."""


def esc(value: Any) -> str:
    if isinstance(value, Html):
        return str(value)
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def layout(
    title: str, body: str, active: str = "", csrf: str | None = None,
    show_nav: bool = True,
) -> str:
    """Shared page wrapper: PWA tags, viewport, responsive nav (CSS-only hamburger)."""
    nav = ""
    if show_nav:
        links = "".join(
            f'<a href="{href}"{" class=on" if href == active else ""}>{label}</a>'
            for href, label in NAV
        )
        logout = (
            f'<form method="post" action="/logout"><input type="hidden" '
            f'name="csrf" value="{esc(csrf)}"><button>Log out</button></form>'
            if csrf else ""
        )
        nav = (
            '<input type="checkbox" id="nav-toggle" class="nav-toggle" aria-label="Menu">'
            '<header class="topbar"><label for="nav-toggle" class="burger" '
            'aria-label="Toggle menu">☰</label><span class="brand">pAIrtner</span></header>'
            f'<nav class="side">{links}{logout}</nav>'
        )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta name=theme-color content='#e94560'>"
        '<link rel="manifest" href="/static/manifest.json" crossorigin="use-credentials">'
        '<link rel="icon" href="/static/icon-192.png">'
        f"<title>{esc(title)} · pAIrtner admin</title><style>{_CSS}</style></head>"
        f'<body><div class="shell">{nav}<main><h1>{esc(title)}</h1>{body}</main></div>'
        '<script src="/static/pwa.js" defer></script></body></html>'
    )


def table(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    out = []
    for row in rows:
        cells = []
        for v in row:
            if isinstance(v, Html):
                cells.append(f"<td>{v}</td>")
                continue
            s = "" if v is None else str(v)
            if len(s) > MAX_CELL_CHARS:
                s = s[:MAX_CELL_CHARS] + "…"
            cells.append(f"<td>{esc(s)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    if not out:
        out.append(f"<tr><td colspan={max(1, len(columns))} class=muted>no rows</td></tr>")
    return f'<div class="tscroll"><table><tr>{head}</tr>{"".join(out)}</table></div>'


def cards(items: Iterable[tuple[str, Any]]) -> str:
    return '<div class="cards wide">' + "".join(
        f'<div class="card"><b>{esc(v)}</b>{esc(k)}</div>' for k, v in items
    ) + "</div>"


def svg_bars(series: Sequence[tuple[str, int]], width: int = 700, height: int = 120) -> str:
    """Server-rendered bar chart; no JS."""
    if not series:
        return "<p class=muted>no data</p>"
    peak = max(v for _, v in series) or 1
    gap = 3
    bar_w = max(2, (width - gap * len(series)) / len(series))
    bars = []
    for i, (label, value) in enumerate(series):
        h = (value / peak) * (height - 20)
        x = i * (bar_w + gap)
        bars.append(
            f'<rect x="{x:.1f}" y="{height - 16 - h:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" fill="#4a7bd0"><title>{esc(label)}: {esc(value)}</title></rect>'
        )
    first, last = esc(series[0][0]), esc(series[-1][0])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'role="img">{"".join(bars)}'
        f'<text x="0" y="{height - 3}" font-size="10" fill="#6b7280">{first}</text>'
        f'<text x="{width}" y="{height - 3}" font-size="10" text-anchor="end" '
        f'fill="#6b7280">{last} (max {peak})</text></svg>'
    )


def link(href: str, text: Any) -> Html:
    return Html(f'<a href="{esc(href)}">{esc(text)}</a>')
