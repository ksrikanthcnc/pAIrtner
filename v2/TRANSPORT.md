# Transport & UI Design

Adapters are thin. They convert protocol → internal message → protocol. All logic lives in the core.

## Adapters (v2, slimmed)

| Adapter | Purpose | Keep from v1? |
|---|---|---|
| Telegram | Primary mobile interface | Yes, simplify |
| REST + PWA | Browser testing, demo, self-hosted web | Yes, simplify |
| CLI | Dev testing | Yes |
| WebSocket | Real-time PWA | Optional (REST long-poll is good enough) |

### Adapter interface

```python
class Adapter(Protocol):
    async def send_message(self, user_id: int, text: str) -> None: ...
    async def send_buttons(self, user_id: int, text: str, buttons: list) -> None: ...
    def owns_user(self, user_id: int) -> bool: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

That's it. No adapter-specific logic. The router handles dispatch, commands, memory ops.

## PWA (v2)

Keep the current PWA approach (lightweight, no framework) but add:

### Memory panel

New tab/screen in the PWA:
- List facts by kind (core | working | episodes)
- Each fact shows: text, score bar, citation count, age, state badge
- Tap to expand: provenance (source message), version history
- Swipe actions: archive, promote to core, edit text
- Search bar (FTS5 via REST API)
- Health indicator: "🧠 47 facts (12 core, 30 working, 5 episodes) | 3 stale"

### Admin dashboard (v2)

Keep the read-only SQLite explorer. Add:
- Memory health dashboard (per-user scores, stale facts, contradictions)
- Knowledge rebuild button (re-extract from raw)
- Extraction worker status (last run, messages processed, facts created)

## Commands (v2, reduced)

v1 has 20 commands. v2 has 10:

| Command | Purpose | v1 equivalent |
|---|---|---|
| `/start` | Pairing | same |
| `/join CODE` | Join partner | same |
| `/help` | Show commands | same |
| `/tell TEXT` | Draft relay | same |
| `/memory` | View/search/manage facts | `/whatdoyouknow` + `/review` + `/search` + `/forget` |
| `/style` | Voice + mood settings | same (already merged) |
| `/goal` | Personal + shared goals | same (already merged) |
| `/status` | Quick dashboard | same |
| `/settings` | All config | same |
| `/export` | Data backup | same |

Dropped: `/mediate` (move to a button/mode), `/remember` (auto, not needed), `/yolo` (fold into `/settings`), `/conflicts`, `/timeline` (show in memory panel), `/history`, `/usage`, `/pause`/`/resume` (fold into `/settings`).

Tags stay: `#send`, `#keep`, `#blunt`, `#honest`.

## GitHub Pages demo (v2)

Keep the static client-side demo. Improve:
- Match the actual PWA styling
- Add a memory panel (localStorage-backed)
- Show the "learning" process visually (fact extraction highlighted)
- Instructions for getting a Gemini API key
