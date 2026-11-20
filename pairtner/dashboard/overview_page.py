"""Admin overview page and couple-detail building blocks (server-rendered)."""
from __future__ import annotations

from typing import Any

from pairtner.dashboard.render import Html, cards, esc, link, table

QUICK_LINKS = [("/couples", "💑 Couples"), ("/users", "👤 Users"),
               ("/queue", "📬 Queue"), ("/usage", "📈 Usage")]


def _quick_links(extra: list[tuple[str, str]] | None = None) -> str:
    items = QUICK_LINKS + (extra or [])
    return '<div class="cards">' + "".join(
        f'<a class="card" href="{esc(h)}">{esc(t)}</a>' for h, t in items
    ) + "</div>"


def activity_feed(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="muted">No recent activity.</p>'
    items = []
    for e in events:
        text = f"<div>{esc(e['text'])}</div>" if e.get("text") else ""
        items.append(
            f'<li><time>{esc(e["at"])} UTC · {esc(e["kind"])}</time>'
            f'<div>{esc(e["label"])}</div>{text}</li>'
        )
    return f'<ul class="feed">{"".join(items)}</ul>'


def render_overview(o: dict[str, Any], health: dict[str, Any],
                    events: list[dict[str, Any]]) -> str:
    avg = health.get("average")
    health_card = (f"{avg}/100" if avg is not None else "n/a")
    health_sub = f"{health.get('scored', 0)} scored"
    summary = (
        '<div class="cards">'
        f'<div class="card"><b>{esc(o["couples"])}</b>active couples'
        f'<span class="sub"> · {esc(o["users"])} users</span></div>'
        f'<div class="card"><b>{esc(o["messages_total"])}</b>messages total'
        f'<span class="sub"> · {esc(o["messages_24h"])} in 24h</span></div>'
        f'<div class="card"><b>{esc(o["facts_active"])}</b>active facts'
        f'<span class="sub"> · {esc(o["facts_total"])} total</span></div>'
        f'<div class="card"><b>{esc(health_card)}</b>avg health score'
        f'<span class="sub"> · {esc(health_sub)}</span></div>'
        '</div>'
    )
    return (summary + "<h2>Quick links</h2>" + _quick_links()
            + "<h2>Recent activity</h2>" + activity_feed(events))


def render_couple(
    couple_id: str, a: int, b: int, partners: dict[int, dict[str, Any]],
    health: dict[str, Any] | None, quick: dict[str, Any], events_table: str,
) -> str:
    def profile(uid: int) -> str:
        p = partners.get(uid) or {}
        return (
            f'<div class="card"><b>{link(f"/users/{uid}", p.get("name") or f"User {uid}")}</b>'
            f'<span class="sub">id {esc(uid)} · {esc(p.get("messages", 0))} messages · '
            f'{esc(p.get("facts", 0))} facts<br>last seen {esc(p.get("last_seen") or "never")}'
            "</span></div>"
        )

    nudges = quick.get("pending_nudges")
    stats = cards([
        ("messages this week", quick["messages_week"]),
        ("active facts", quick["active_facts"]),
        ("pending nudges", "n/a" if nudges is None else nudges),
    ])
    if health and isinstance(health.get("total"), (int, float)):
        dims = [(k, v) for k, v in health.items()
                if k not in ("total", "computed_at", "window_days")
                and isinstance(v, (int, float)) and not isinstance(v, bool)]
        rows = "".join(
            f'<div class="dim"><span>{esc(k)}</span><span class="bar">'
            f'<i style="width:{max(0, min(100, round(v)))}%"></i></span>'
            f'<b>{esc(round(v))}</b></div>' for k, v in dims
        )
        hb = (f'<div class="card"><b>{esc(round(health["total"]))}/100</b>health score'
              f'<span class="sub"> · computed {esc(health.get("computed_at") or "unknown")}</span>'
              f'<div class="dims">{rows}</div></div>')
    else:
        hb = '<p class="muted">No health score computed yet.</p>'
    links = (f'<p>{link(f"/timeline/{a}_{b}", "🕒 Timeline")} · '
             f'{link(f"/conversations/{couple_id}", "💬 View Conversation")}</p>')
    return (
        links + '<div class="cols">' + profile(a) + profile(b) + "</div>"
        + "<h2>Health</h2>" + hb + "<h2>This week</h2>" + stats
        + "<h2>Timeline</h2>" + events_table
    )


__all__ = ["render_overview", "render_couple", "activity_feed", "Html", "table"]
