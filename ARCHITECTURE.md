# pAIrtner — Architecture & Design Reference

A comprehensive doc for AI agents, reviewers, or new contributors.

## What is pAIrtner?

An AI-mediated communication assistant for relationships. Two (or more) people each chat privately with the bot. The bot:
- Remembers per-person facts and context
- Rewrites hard messages gently (logarithmic dampening)
- Delivers messages only after the sender explicitly approves
- Learns each user's communication style over time
- Provides neutral mediation using both sides' perspectives
- Tracks goals, conflicts, and relationship patterns
- Teaches better articulation progressively

Not a therapist. Not a judge. A warm, practical friend who helps people communicate without starting fires.

## System Architecture

```
┌──────────────────────────────────────────────────┐
│  Transport Layer (pluggable adapters)            │
│  ┌──────────┐ ┌───────────┐ ┌─────┐            │
│  │ Telegram  │ │ WebSocket │ │ CLI │            │
│  │ (live)    │ │ (PWA/dev) │ │(dev)│            │
│  └────┬──────┘ └─────┬─────┘ └──┬──┘            │
│       └──────────────┼──────────┘                │
│                      ▼                           │
│  ┌──────────────────────────────────────┐        │
│  │  BotService (transport-agnostic)     │        │
│  │  ┌─────────────┐ ┌────────────────┐  │        │
│  │  │DispatchMixin│ │ CommandsMixin  │  │        │
│  │  │(route cmds) │ │ (/start,/help) │  │        │
│  │  └─────────────┘ └────────────────┘  │        │
│  │  ┌─────────────┐ ┌────────────────┐  │        │
│  │  │ TagsMixin   │ │CallbacksMixin  │  │        │
│  │  │(#send,#keep)│ │(approve,edit)  │  │        │
│  │  └─────────────┘ └────────────────┘  │        │
│  └───────────────────┬──────────────────┘        │
│                      │                           │
│  ┌───────────────────┴──────────────────┐        │
│  │  Support Modules                     │        │
│  │  onboarding · sanitize · dampening   │        │
│  │  profile_updater · conflict_detector │        │
│  │  feedback · nudges · therapy_export  │        │
│  └───────────────────┬──────────────────┘        │
│                      │                           │
│  ┌───────────┐  ┌────┴─────┐  ┌──────────────┐  │
│  │ Prompts   │  │ LLM      │  │ DB (SQLite)  │  │
│  │ (md files)│  │ (pluggable│  │ aiosqlite    │  │
│  │ persona,  │  │  Gemini / │  │ 15+ tables   │  │
│  │ voices,   │  │  OpenAI / │  │              │  │
│  │ tasks)    │  │  Ollama)  │  │              │  │
│  └───────────┘  └──────────┘  └──────────────┘  │
│                                                  │
│  ┌──────────────────────────────────────┐        │
│  │  Admin Dashboard (separate process)  │        │
│  │  Stats · SQLite explorer · Actions   │        │
│  └──────────────────────────────────────┘        │
│                                                  │
│  ┌──────────────────────────────────────┐        │
│  │  Dev Tooling (dev/localtest/)        │        │
│  │  run_local.py · fake_llm.py ·       │        │
│  │  scenarios.py · web chat UI          │        │
│  └──────────────────────────────────────┘        │
└──────────────────────────────────────────────────┘
```

## Data Model

### Core Tables
- `users(tg_id, name, partner_id, lang, paused, created_at)` — paired users
- `facts(id, owner_id, text, visibility, category, fact_type, source, expires_at, archived_at, supersedes_id, created_at)` — per-user knowledge with lifecycle
- `pending(id, sender_id, draft, edited_draft, status, send_after, feedback, acknowledged_at, created_at)` — relay approval pipeline
- `messages(id, user_id, role, content, created_at)` — rolling chat history
- `usage(date, model, count)` — per-model daily API usage

### Intelligence Tables
- `user_profiles(user_id, default_voice, voice_intensity, dampening_base, onboarding_done/step, relationship_type, duration_years, has_kids, age_range, love_story_brief, learned_voice, message_count)` — per-user profile with learned voice traits
- `user_mood_styles(id, user_id, mood, voice, dampening, notes)` — mood-specific overrides
- `user_patterns(id, user_id, pattern_type, pattern, frequency, first_seen, last_seen, status)` — detected communication patterns
- `partner_perceptions(id, user_id, about_id, theme, sentiment, confidence)` — what bot knows about partner from user's messages
- `user_goals(id, user_id, goal, status, progress)` — personal goals
- `shared_goals(id, couple_id, goal, status, progress)` — couple goals

### Engagement Tables
- `conflicts(id, couple_id, theme, status, relay_ids, opened_at, resolved_at)` — tracked discussion topics
- `timeline_events(id, couple_id, event_type, summary, metadata)` — relationship timeline
- `interaction_scores(id, couple_id, user_id, score_type, value, period_start)` — Gottman 5:1 ratio tracking
- `checkin_sessions(id, couple_id, status, questions)` — weekly check-in state

### Infrastructure Tables
- `couple_config(couple_id, config_key, config_value)` — per-couple settings (BYO Gemini key, Fernet-encrypted)
- `auth_tokens(user_id, token)` — WebSocket auth
- `pending_batch(id, sender_id, receiver_id, status, send_after)` — cooldown accumulation
- `pending_batch_items(id, batch_id, pending_id)` — batch membership
- `rate_limit(user_id, minute_key, minute_count, day_key, day_count)` — per-user rate limiting
- `fact_summaries(id, user_id, month, summary_text)` — monthly compressed summaries
- `facts_fts` — FTS5 virtual table for full-text search

## Privacy Model

1. **Structural isolation at SQL level**: User X's prompt query fetches only X's private facts + shared facts. Partner's private facts are never in the query.
2. **Partner summaries, not raw text**: The other user's perspective appears as auto-extracted themes (`partner_perceptions`), never as verbatim messages.
3. **LLM cannot publish facts**: `_save_facts` forces `visibility=private` regardless of what the model returns. Only an explicit user action (planned [Share] button) can make a fact shared.
4. **Approval gate**: Every relay must be explicitly approved by the sender. Discarded relays are never delivered.
5. **Sender verification**: Only the sender can approve/edit/discard their own pending relay.
6. **Input sanitization**: Injection-pattern detection strips known prompt-injection prefixes before they reach the LLM.
7. **Emergency override**: Crisis language detection fires before rate limits and all other processing, immediately providing helpline resources.
8. **Allowlist**: `ALLOWED_USER_IDS` silently ignores all interaction from unrecognized Telegram users.

## Prompt Assembly

```
System instruction:
  prompts/persona.md                    ← bot personality (editable)
  + prompts/voices/{style}.md           ← cultural voice preset
  + prompts/tasks/{task}.md             ← task-specific instructions
  + dampening level + mood + learned voice profile

User context:
  User's facts (filtered by lifecycle stage: fresh > active > relevant)
  User's recent messages (last N)
  Partner summary (themes from partner_perceptions, NOT raw text)
  Shared facts
  Active goals
  Relationship metadata (from user_profiles)

Current message (sanitized)

Expected output: JSON
  {reply, new_facts[], relay_draft, detected_mood,
   patterns_observed[], partner_themes[], articulation_note,
   horsemen_detected[], voice_traits{}}
```

## Key Flows

### Relay (message to partner)
```
User sends "#send you never help" →
  LLM rewrites gently → shows draft with [Approve][Edit][Discard] →
  User approves → cooldown timer starts →
  If more relays within cooldown: accumulate, reset timer →
  After cooldown: "Review before sending?" [Send All][Review Each][Discard All] →
  User confirms → delivered to partner →
  Partner responds → sender gets "Did that land well?" feedback
```

### Fact Lifecycle
```
New fact → Fresh (0-7d, always in prompt)
        → Active (7-30d, in prompt)
        → Aging (30-90d, ask user or auto-demote)
        → Archived (90+d, compressed into monthly summary)
Contradiction: new fact vs existing → ask user, archive old one
```

### Dampening Curve
```
Logarithmic: harsh inputs get heavily softened, mild inputs barely touched.
Per-user base × mood modifier × time decay (months of use).
#blunt tag bypasses dampening entirely.
```

## Commands (26+)

**Basic**: /start, /join, /help, /lang, /status, /usage, /skip
**Communication**: /tell, /mediate, /pause, /resume, /history, /conflicts, /timeline
**Memory**: /whatdoyouknow, /forget, /export, /export therapy, /remember, /search
**Goals**: /goal, /together, /goals
**Personalization**: /settings, /vibe, /style, /mood, /setup

**Tags**: #keep, #send, #deep, #blunt, #now

## LLM Providers

| Provider | Config | Use case |
|---|---|---|
| Gemini (free tier) | `LLM_PROVIDER=gemini` | Production, free |
| OpenAI-compat | `LLM_PROVIDER=openai_compat` | Groq, OpenRouter, Ollama |
| Ollama (local) | `LLM_BASE_URL=http://localhost:11434/v1` | Dev/testing, fully private |
| Fake (deterministic) | `dev/localtest/fake_llm.py` | Plumbing tests, no model needed |

`LLM_REASONING_EFFORT=none` disables thinking on reasoning models (qwen3 via Ollama).

## Adapters

| Adapter | Purpose | Entry |
|---|---|---|
| Telegram | Production chat | `--adapter telegram` |
| WebSocket | PWA/web chat, dev | `--adapter websocket` (port 8080) |
| CLI | Terminal testing | `--adapter cli` |
| Both | Telegram + WebSocket | `--adapter both` |

All adapters use `BotService.dispatch()` for command routing (except Telegram which has its own handler registration for Telegram-specific features like typing indicators).

## Dashboard

Separate process, reads the same SQLite DB. Token-authenticated, LAN/Tailscale only.

- **Stats**: users, messages/day, LLM usage/model, pending relays, conflicts, goals
- **SQLite Explorer**: list tables, browse rows with paging, read-only
- **Admin Actions**: clear user, clear DB, backup now (all require typed confirmation + auto-backup)
- **Redaction**: raw text hidden by default, toggle to reveal (admin-only setting)

Run: `uv run python -m couples_bot.dashboard` or `docker compose up dashboard`

## Dev & Testing

### Local test rig (`dev/localtest/`)
- `run_local.py` — bot + WebSocket + throwaway DB + static chat UI
  - `--llm ollama` (real model) or `--llm fake` (UPPER(input), zero latency)
- `fake_llm.py` — deterministic OpenAI-compat endpoint, deliberately misbehaves (labels facts "shared") to exercise guards
- `scenarios.py` — scripted two-user flows: pairing, onboarding, relay, discard, privacy guards, misc commands
- `web/duo.html` — two-pane browser UI, connects via WebSocket

### Test suite
- 801 tests across 51 test files
- Privacy tests verify partner data never enters the wrong prompt
- Fact guard tests verify LLM can't publish shared facts and duplicates are rejected
- Dashboard tests verify secrets are never rendered and destructive actions require confirmation

## Configuration

`settings.yaml` — all tunables (cooldown, rate limits, digest time, check-in schedule, dampening params, voice dir, backup retention, dashboard settings)

`.env` — secrets only (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, LLM_API_KEY, DB_ENCRYPTION_KEY, DASHBOARD_TOKEN)

## Deployment

| Target | Method |
|---|---|
| Local dev | `uv run python dev/localtest/run_local.py --llm fake` |
| Docker (Win plex-stack) | `docker compose up -d` at `/mnt/e/media/docker/pairtner/` |
| Mac → Win sync | `bash dev/localtest/deploy.sh --rebuild` or rsync with `.rsyncignore` |

## Roadmap (next priorities)

1. **PWA polish**: service worker, display names, HTML tag cleanup, provider-aware privacy text
2. **/mediate redesign**: auto from partner_perceptions + recent activity, time-scoped
3. **[Share] button**: explicit fact sharing from /whatdoyouknow
4. **Periodic flow testing**: digest, check-in, nudge scenarios
5. **Circles model**: rename couples to circles, support any relationship type (parent-child, siblings, friends)
6. **FTS5 + smart context**: when facts exceed threshold, search instead of dump
7. **Gemini function calling**: LLM actively queries facts via tool use

## Stats

- 37 commits, 45 Python modules, 51 test files, 801 tests
- 16 prompt files (persona, 4 voices, 9 tasks, 2 templates)
- 11 dashboard modules
- 5 dev/test files
- ~200 i18n keys (English + Telugu)
- MIT License
