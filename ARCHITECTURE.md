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
│  ┌────────┐ ┌──────────┐ ┌───────────┐ ┌─────┐ │
│  │Telegram│ │REST+PWA  │ │ WebSocket │ │ CLI │ │
│  │(live)  │ │(web, API)│ │ (custom)  │ │(dev)│ │
│  └───┬────┘ └────┬─────┘ └─────┬─────┘ └──┬──┘ │
│      └───────────┼─────────────┼──────────┘     │
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
│  │  scenarios.py · run_all.py (REST)    │        │
│  └──────────────────────────────────────┘        │
└──────────────────────────────────────────────────┘
```

## Data Model

### Core Tables
- `users(tg_id, name, partner_id, lang, paused, created_at)` — paired users
- `facts(id, owner_id, text, visibility, category, fact_type, source, expires_at, archived_at, supersedes_id, shared_at, created_at)` — per-user knowledge with lifecycle. `shared_at` is set when the owner shares a fact and cleared when it is made private; `/mediate` windows use `COALESCE(shared_at, created_at)` so a fact shared today counts even if it was written long ago
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
- `auth_tokens(user_id, token)` — WebSocket and REST auth (REST bearer is `<user_id>:<token>`)
- `rest_outbox(id, user_id, kind, payload, created_at)` — durable per-user event queue for REST clients (relays, review prompts, digests, partner notifications). Read with a cursor through `GET /api/v1/events`, never deleted on read, pruned after `rest.event_retention_days`. The payload holds message text, so the dashboard classifies it as raw text
- `pending_batch(id, sender_id, receiver_id, status, send_after)` — cooldown accumulation
- `pending_batch_items(id, batch_id, pending_id)` — batch membership
- `rate_limit(user_id, minute_key, minute_count, day_key, day_count)` — per-user rate limiting
- `fact_summaries(id, user_id, month, summary_text)` — monthly compressed summaries
- `facts_fts` — FTS5 virtual table for full-text search

## Privacy Model

1. **Structural isolation at SQL level**: User X's prompt query fetches only X's private facts + shared facts. Partner's private facts are never in the query.
2. **Partner summaries, not raw text**: The other user's perspective appears as auto-extracted themes (`partner_perceptions`), never as verbatim messages.
3. **LLM cannot publish facts**: `_save_facts` forces `visibility=private` regardless of what the model returns. Only the owner can make a fact shared, with the 🔓 Share button under each fact in `/whatdoyouknow` (callback `fv:<id>`; the owner check is in the SQL `UPDATE`, so guessing another user's id changes nothing). The button toggles, and the reply states the resulting state.
4. **Approval gate**: Every relay must be explicitly approved by the sender. Discarded relays are never delivered.
5. **Sender verification**: Only the sender can approve/edit/discard their own pending relay.
6. **Input sanitization**: Injection-pattern detection strips known prompt-injection prefixes before they reach the LLM.
7. **Emergency override**: Crisis language detection fires before rate limits and all other processing, immediately providing helpline resources.
8. **Allowlist**: `ALLOWED_USER_IDS` silently ignores all interaction from unrecognized Telegram users. REST users get ids from `2**52` upward; the REST API returns `403 not_allowed` for an id not on a non-empty list.
9. **Mediate inputs are narrow**: `/mediate [window]` sends the model only (a) facts both partners marked shared within the window and (b) each partner's impressions of the other from `partner_perceptions`. Never raw messages, never private facts, never pending drafts. Impressions are unverified guesses, so they pass a deterministic filter at read time: below `mediate.min_confidence` are dropped; first-person themes ("I feel ignored") are dropped because they are the observer talking about themself; themes that closely match the observer's own facts are dropped (token Jaccard >= 0.6, or containment of 4+ words); the rest are de-duplicated, capped at `mediate.max_themes_per_side` and labelled as one person's unverified view. With no input after filtering, the bot answers without calling the model. `mediate.include_perceptions: false` makes it facts-only.
10. **The privacy notice names the real path**: `/start` and `/help` build the notice from `BotService.privacy_info` (transport from `adapter.transport_description`, model from `llm.describe_llm(config)`), so a REST + local-model deployment says messages stay on this server and names neither Telegram nor Google.

## Prompt Assembly

```
System instruction:
  packs/<pack>/persona.md               ← bot personality (editable)
  + packs/<pack>/blocks/*.md            ← role, not-therapy, tone
  + packs/<pack>/voices/{style}.md      ← cultural voice preset
  + packs/<pack>/tasks/{task}.md        ← nudge, appreciation, summarize, therapy export
  + packs/<pack>/strings.yaml           ← every other model-visible sentence and label
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

### Engine vs pack
`pairtner/pack.py` loads and strictly validates a pack at startup (manifest, `engine_contract`, required files, every `strings.yaml` key and its allowed `{placeholders}`); an invalid pack makes the process exit with code 2 before the database is opened. `pack.current()` re-reads changed files on each call (stat-based hot reload). A pack that fails re-validation is ignored and the last good one stays active. The engine keeps block order, separators, markdown markers and numeric thresholds; the pack supplies the words.

### Call tracing
Prompt assembly stores `(task, pack snapshot)` in the `pack.last_trace` context variable. `llm.TracingLLM` wraps the client returned by `create_llm_client`, reads that variable at `generate()` time (so the hash is the one that built the prompt, even if a reload happened in between), logs `llm_call task= model= pack=name@version pack_hash= prompt_sha=` on logger `pairtner.trace`, and writes `llm_calls` (task, model, pack_hash, prompt_sha256) and `pack_snapshots` (pack_hash, name, version, engine_contract, files_json of per-file sha256). `pack_loaded` lines are logged at startup and when a reload changes the hash. Tables hold hashes only: no prompt text, no user id. Database errors are logged as warnings and never fail the call.

### Pack evaluation
`dev/prompt-eval/run_eval.py` (helpers in `eval_lib.py`) drives a pack's `eval/cases.yaml` through the real engine. It starts the dev rig in-process (`dev/localtest/rig.py`: throwaway SQLite DB, `RestAdapter` on 127.0.0.1, Telegram token forced to a dummy, adapter forced to `rest`), registers two paired users per case over REST, seeds context with `pairtner.db` calls, and sends one message as user A. The LLM client is `TracingLLM(Throttle(inner), on_call=observer)`, so each case records the system prompt, user content, parsed JSON and pack hashes of its calls. The runner records new rows in `facts`, `partner_perceptions` and `user_patterns` and echoes the case's `expect` lists; it makes no pass/fail verdict. When the engine returns `FALLBACK_REPLY` (quota or provider outage) the run stops, writes partial results and exits with code 3. `rig.make_gemini_config` loads the real key from the main checkout's `.env` through `load_config` only. Output goes to `dev/prompt-eval/runs/<pack>/<version>/<time>_<llm>-<model>/`. See `docs/PACKS.md`.
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
| REST + PWA | JSON API for scripts and agents, plus an installable web client for people who avoid Telegram | `--adapter rest` (port 8780) |
| Telegram + REST | Both transports, one set of background loops | `--adapter telegram+rest` |
| WebSocket | Custom chat clients | `--adapter websocket` (port 8080) |
| CLI | Terminal testing | `--adapter cli` |
| Both | Telegram + WebSocket | `--adapter both` |
All adapters use `BotService.dispatch()` for command routing (except Telegram which has its own handler registration for Telegram-specific features like typing indicators). `BotService` is transport-unaware.

### REST adapter and web client
- `pairtner/rest/` holds a pure `RestApi.handle(request) -> Response` (stdlib `ThreadingHTTPServer` in a daemon thread, bridged to the event loop), id range, throttles and the markup converter. `pairtner/adapters/rest_adapter.py` runs it.
- Auth: bearer `<user_id>:<secret>`; ids start at `REST_ID_BASE = 2**52` so they never collide with Telegram ids and stay exact in JavaScript.
- Delivery to clients that may be offline goes through the `rest_outbox` table and long-poll (`GET /api/v1/events?after=N&wait=S`). Plain HTTP, no streaming client needed, nothing is lost if the tab is closed.
- Bot text reaches the browser as a `Msg` with `text` (plain) and `markup` (only `b i u code pre` allowed). `pairtner/web/render.js` builds DOM nodes and never uses `innerHTML`; the CSP forbids inline scripts. A test greps for the banned APIs.
- The web client lives in `pairtner/web/` (vanilla JS, no build step) and is served from a fixed file list.
- Full contract for agents: [docs/REST_API.md](docs/REST_API.md).

### MultiAdapter and background loops
`adapters/loops.py::start_background_loops` creates the delivery, digest, check-in, nudge and backup loops (intervals from the `loops:` settings). `MultiAdapter` (`telegram+rest`) starts them once with a send function that routes by `owns_user` (REST for ids >= `2**52`, Telegram below) and starts the children without their own loops.

Known limitation: `--adapter both` (Telegram + WebSocket) still starts the loops once per adapter. Two delivery loops race, and the one that cannot reach a user still marks the relay as sent. Use `telegram+rest` for combined deployments.

## Dashboard

Separate process, reads the same SQLite DB. Token-authenticated, LAN/Tailscale only.

- **Stats**: users, messages/day, LLM usage/model, pending relays, conflicts, goals
- **SQLite Explorer**: list tables, browse rows with paging, read-only
- **Admin Actions**: clear user, clear DB, backup now (all require typed confirmation + auto-backup)
- **Redaction**: raw text hidden by default, toggle to reveal (admin-only setting)

Run: `uv run python -m pairtner.dashboard` or `docker compose up dashboard`

## Dev & Testing

### Local test rig (`dev/localtest/`)
- `run_local.py` — real bot with the REST adapter + throwaway DB, serving the product web client (`/`) and `/duo.html` (two panes, one per user)
  - `--llm ollama` (real model, for flow checks only) or `--llm fake` (UPPER(input), zero latency); `--port` (8766), `--fake-port` (11435)
- `rig.py` — shared start/stop helpers that build a `Config` directly (no `.env`, no yaml)
- `fake_llm.py` — deterministic OpenAI-compat endpoint, deliberately misbehaves (labels facts "shared") to exercise guards; records calls at `GET /_calls` so scenarios can assert what the model was sent; markers `[theme]` and `[selfstmt]` produce fixed impressions
- `scenarios.py` — stdlib REST client and scripted two-user flows: `pair_onboard facts_privacy relay discard misc guards rest_session mediate share_toggle relay_no_review periodic`. Prints `===== FLAGS =====`, exit 1 on any flag
- `run_all.py` — three phases (review on, review off, periodic digest/check-in/nudge/backup) and expects `FLAGS` then `- none`
### Test suite
- 1008 tests across 64 test files (the JS renderer tests run through `node --test tests/js/` and are skipped if Node is missing)
- `tests/test_docs.py` keeps the docs honest: every route and error code in `rest/api.py` appears in `docs/REST_API.md` and vice versa, and every documented `REST_*`, `LOOPS_*`, `MEDIATE_*` name is read in `config.py`
- Privacy tests verify partner data never enters the wrong prompt
- Fact guard tests verify LLM can't publish shared facts and duplicates are rejected
- Dashboard tests verify secrets are never rendered and destructive actions require confirmation

## Configuration

`settings.yaml` — all tunables (cooldown, rate limits, digest time, check-in schedule, dampening params, voice dir, backup retention, dashboard settings)

`.env` — secrets only (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, LLM_API_KEY, DB_ENCRYPTION_KEY, DASHBOARD_TOKEN, REST_INVITE_CODE). New blocks: `rest:` (host, port, registration, limits), `loops:` (background loop intervals) and `mediate:` (window and filter settings); each has `REST_*`, `LOOPS_*`, `MEDIATE_*` env overrides.

## Deployment

| Target | Method |
|---|---|
| Local dev | `uv run python dev/localtest/run_local.py --llm fake` |
| Web app only | `ADAPTER=rest REST_INVITE_CODE=... uv run python -m pairtner`; HTTPS via Tailscale `serve` or a proxy to install the PWA (see DEPLOYMENT.md) |
| Docker (any host) | `docker compose up -d` in the project folder on the host |
| Mac → Win sync | `bash dev/deploy.sh --rebuild` or rsync with `.rsyncignore` |

## Roadmap (next priorities)
Done in the REST + PWA batch: PWA with service worker and display names, provider-aware privacy text, time-scoped `/mediate`, the [Share] button, scripted periodic-flow scenarios.
1. **Web Push** for the PWA (clients long-poll while open today)
2. **Hash stored auth tokens** (shared with the WebSocket adapter)
3. **Circles model**: rename couples to circles, support any relationship type (parent-child, siblings, friends)
4. **FTS5 + smart context**: when facts exceed threshold, search instead of dump
5. **Gemini function calling**: LLM actively queries facts via tool use

## Stats

- 44 commits, 54 Python modules, 64 test files, 1008 tests
- 16 prompt files (persona, 4 voices, 9 tasks, 2 templates)
- 11 dashboard modules
- 5 dev/rig files (`dev/localtest/`) plus `dev/make_icons.py` and `dev/deploy.sh`
- ~200 i18n keys (English + Telugu)
- MIT License
