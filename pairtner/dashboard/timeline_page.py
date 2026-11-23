"""Server-rendered admin timeline page (chat-style, colour-coded cards).

The dashboard CSP forbids scripts, so filters are CSS-only (checkboxes +
``:has()``) and "Load older" is a plain link driven by the ``before`` cursor.
"""
from __future__ import annotations

from typing import Any

from pairtner.dashboard.render import esc

# type -> (filter group, icon, label, css class)
_META = {
    "message": ("msg", "💬", "Message", "msg"),
    "bot_reply": ("msg", "🤖", "pAIrtner", "bot"),
    "fact": ("fact", "🧠", "Fact", "fact"),
    "nudge": ("nudge", "🔔", "Nudge", "nudge"),
    "summary": ("summary", "📊", "Summary", "summary"),
    "archival": ("archive", "🗄️", "Archived", "archive"),
    "setting": ("setting", "⚙️", "Setting", "setting"),
}

FILTERS = [("msg", "💬 Messages"), ("fact", "🧠 Facts"), ("nudge", "🔔 Nudges"),
           ("summary", "📊 Summaries"), ("archive", "🗄️ Archival"),
           ("setting", "⚙️ Settings")]

CSS = """
<style>
.tl{max-width:760px;margin:0 auto;--blue:#e8f0fe;--blue-b:#bcd0f7;--green:#e6f6ec;--green-b:#9ad4ae;
--orange:#fff1e0;--orange-b:#f5c58d;--purple:#f1e9fb;--purple-b:#cdb3ee;--gray:#eceef1;--gray-b:#c9ced6;
--bot:#2f3a5a;}
.tl-bar{position:sticky;top:0;z-index:5;background:#f6f7f9ee;padding:8px 0;display:flex;flex-wrap:wrap;gap:6px;
backdrop-filter:blur(4px);border-bottom:1px solid #dde1e7}
.tl-bar input{position:absolute;opacity:0;pointer-events:none}
.tl-bar label{cursor:pointer;padding:4px 12px;border-radius:999px;background:#fff;border:1px solid #dde1e7;
font-size:13px;user-select:none}
.tl-bar input:checked+label{background:#1c1f24;color:#fff;border-color:#1c1f24}
.tl-bar input:focus-visible+label{outline:2px solid #4a7bd0}
.tl-day{text-align:center;margin:18px 0 6px;font-size:12px;color:#6b7280;letter-spacing:.04em}
.tl-day span{background:#e9ebef;padding:2px 12px;border-radius:999px}
.ev{margin:6px 0;display:flex}
.ev .box{max-width:82%;border-radius:14px;padding:8px 12px;border:1px solid;box-shadow:0 1px 2px #0000000d;
overflow-wrap:anywhere}
.ev .hd{font-size:11px;font-weight:600;opacity:.75;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.ev .hd time{font-weight:400;opacity:.8;margin-left:auto}
.ev .tx{white-space:pre-wrap;margin-top:3px}
.ev-msg .box{background:var(--blue);border-color:var(--blue-b);border-bottom-left-radius:4px}
.ev-bot{justify-content:flex-end}
.ev-bot .box{background:var(--bot);color:#fff;border-color:var(--bot);border-bottom-right-radius:4px}
.ev-bot .hd{opacity:.8}
.ev-fact .box,.ev-nudge .box,.ev-summary .box,.ev-archive .box,.ev-setting .box{max-width:92%;margin:0 auto;width:92%}
.ev-fact,.ev-nudge,.ev-summary,.ev-archive,.ev-setting{justify-content:center}
.ev-fact .box{background:var(--green);border-color:var(--green-b)}
.ev-nudge .box{background:var(--orange);border-color:var(--orange-b)}
.ev-summary .box{background:var(--purple);border-color:var(--purple-b)}
.ev-archive .box,.ev-setting .box{background:var(--gray);border-color:var(--gray-b);font-size:13px}
.badge{display:inline-block;font-size:10px;padding:1px 8px;border-radius:999px;background:#ffffffb0;
border:1px solid #0002;font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.st-pending{background:#fff3cd}.st-delivered{background:#d4f1dc}
.st-cancelled{background:#fde2e2}.st-expired{background:#e5e7eb}
.vis-private{background:#fde2e2}.vis-shared{background:#fff}
.bar{height:5px;border-radius:3px;background:#0001;margin-top:6px;overflow:hidden}
.bar i{display:block;height:100%;background:#2f9e5f}
details summary{cursor:pointer}
.tl-more{text-align:center;margin:18px 0}
.tl-more a{display:inline-block;padding:8px 20px;border-radius:999px;background:#1c1f24;color:#fff;text-decoration:none}
.tl-empty{text-align:center;color:#6b7280;margin:40px 0}
""" + "".join(
    f'.tl:has(#f-{g}:not(:checked)) .g-{g}{{display:none}}\n' for g, _ in FILTERS
) + """
@media (max-width:1023px){.tl-bar{top:44px}}
@media (max-width:600px){.tl{font-size:13px}.ev .tx{font-size:13px}.ev .hd{font-size:10px}.tl-bar label{padding:3px 9px;font-size:12px}
.ev .box{max-width:94%}.ev-fact .box,.ev-nudge .box,.ev-summary .box,
.ev-archive .box,.ev-setting .box{width:98%;max-width:98%}.ev .hd time{margin-left:0}}
@media (prefers-color-scheme:dark){.tl-bar{background:#14161aee}}
</style>
"""


def _time(ts: str) -> str:
    return f'<time datetime="{esc(ts)}">{esc(ts[11:16])}</time>'


def _badge(text: Any, cls: str = "") -> str:
    return f'<span class="badge {esc(cls)}">{esc(text)}</span>' if text else ""


def _card(ev: dict[str, Any]) -> str:
    group, icon, label, css = _META.get(ev["type"], ("setting", "•", ev["type"], "setting"))
    ts = ev["ts"]
    t = ev["type"]
    name = ev.get("user")
    head = f"{icon} {esc(label)}"
    body = ""
    if t == "message":
        head = f"{icon} {esc(name or 'User')}"
        body = esc(ev.get("content"))
    elif t == "bot_reply":
        head = f"{icon} pAIrtner" + (f" → {esc(ev['to'])}" if ev.get("to") else "")
        body = esc(ev.get("content"))
    elif t == "fact":
        pct = max(0, min(100, round(float(ev.get("intensity") or 0) * 100)))
        head += f" · {esc(name)}" if name else ""
        head += " " + _badge(ev.get("visibility"), f"vis-{ev.get('visibility')}")
        body = (f"{esc(ev.get('text'))}"
                f'</div><div class="bar" title="intensity {pct}%"><i style="width:{pct}%"></i>')
    elif t == "nudge":
        head += " " + _badge(ev.get("nudge_type")) + " " + _badge(
            ev.get("status"), f"st-{ev.get('status')}")
        body = esc(ev.get("suggestion"))
    elif t == "summary":
        text = str(ev.get("narrative") or "")
        first = text.split("\n", 1)[0]
        short = first if len(first) <= 140 else first[:140] + "…"
        head += " " + _badge(ev.get("kind")) + (f" · {esc(name)}" if name else "")
        body = (f"<details><summary>{esc(short)}</summary>"
                f'<div class="tx">{esc(text)}</div></details>')
    elif t == "archival":
        head += " " + _badge(ev.get("kind"))
        body = esc(ev.get("text"))
    else:
        head += f" · {esc(ev.get('key'))}"
        body = esc(ev.get("value"))
    if t == "summary":
        inner = f'<div class="hd">{head}{_time(ts)}</div>{body}'
    else:
        inner = f'<div class="hd">{head}{_time(ts)}</div><div class="tx">{body}</div>'
    return f'<div class="ev ev-{css} g-{group}"><div class="box">{inner}</div></div>'


def render_timeline(
    rid: str, events: list[dict[str, Any]], *, limit: int, before: str | None,
    has_more: bool, names: str = "",
) -> str:
    bar = "".join(
        f'<input type="checkbox" id="f-{g}" checked><label for="f-{g}">{esc(lbl)}</label>'
        for g, lbl in FILTERS
    )
    parts: list[str] = []
    day = None
    for ev in events:
        d = ev["ts"][:10]
        if d != day:
            day = d
            parts.append(f'<div class="tl-day"><span>{esc(d)}</span></div>')
        parts.append(_card(ev))
    if not events:
        parts.append('<p class="tl-empty">No events to show.</p>')
    more = ""
    if has_more and events:
        more = (f'<div class="tl-more"><a rel="nofollow" href="/timeline/{esc(rid)}'
                f'?limit={limit}&amp;before={esc(events[0]["ts"])}">⬆ Load older events</a></div>')
    head = (f'<p><a href="/couples/{esc(rid.replace("_", ":"))}">← Back to couple</a>'
            f' · <a href="/api/admin/timeline/{esc(rid)}?limit={limit}'
            + (f'&amp;before={esc(before)}' if before else "")
            + '">JSON</a></p>')
    return (CSS + head + f'<div class="tl"><div class="tl-bar">{bar}</div>{more}'
            + "".join(parts) + "</div>")
