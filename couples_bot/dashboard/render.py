"""Tiny HTML helpers: escaping, layout, tables, inline-SVG bar charts."""
from __future__ import annotations

import html
from typing import Any, Iterable, Sequence

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}

MAX_CELL_CHARS = 400

_CSS = """
body{font:14px/1.45 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1c1f24}
header{background:#1c1f24;padding:8px 16px}
header a{color:#cfd6e0;margin-right:14px;text-decoration:none}
header a.on{color:#fff;font-weight:600}
header form{display:inline;float:right}
main{padding:16px;max-width:1200px;margin:auto}
h1{font-size:20px} h2{font-size:16px;margin-top:24px}
.cards{display:flex;flex-wrap:wrap;gap:10px}
.card{background:#fff;border:1px solid #dde1e7;border-radius:6px;padding:10px 14px;min-width:130px}
.card b{display:block;font-size:22px}
table{border-collapse:collapse;background:#fff;width:100%;margin:8px 0}
th,td{border:1px solid #dde1e7;padding:4px 8px;text-align:left;vertical-align:top;font-size:13px;word-break:break-word}
th{background:#eef0f4}
pre{background:#fff;border:1px solid #dde1e7;padding:8px;overflow:auto}
.warn{background:#fff3cd;border:1px solid #e6cf7a;padding:8px;border-radius:6px}
.err{background:#fde2e2;border:1px solid #e39a9a;padding:8px;border-radius:6px}
.ok{background:#e2f5e2;border:1px solid #8fcf8f;padding:8px;border-radius:6px}
input[type=text],input[type=password]{padding:4px;width:22em}
button{padding:4px 10px}
.muted{color:#6b7280}
"""

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
        nav = f"<header>{links}{logout}</header>"
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)} · pAIrtner admin</title><style>{_CSS}</style></head>"
        f"<body>{nav}<main><h1>{esc(title)}</h1>{body}</main></body></html>"
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
    return f"<table><tr>{head}</tr>{''.join(out)}</table>"


def cards(items: Iterable[tuple[str, Any]]) -> str:
    return '<div class="cards">' + "".join(
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
