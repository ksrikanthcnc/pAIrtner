# pAIrtner

A private bot that helps couples communicate more gently. Each partner chats privately with the bot — it softens messages, learns your communication style, and only passes along what you approve.

## Philosophy

- **Data is gold** — raw conversations are stored permanently. LLMs are replaceable; your relationship history is the product.
- **Culturally adaptive** — no hardcoded Western therapy-speak. The bot learns your natural register (Telugu, Hindi, casual English, etc.) and matches it.
- **Progressively useful** — the bot teaches you to communicate better over time, becoming less necessary as you grow.
- **Privacy structural** — partner data isolation is enforced at the SQL level, not prompt instructions. The LLM never sees your partner's raw messages.

## Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- A Gemini API Key (from [Google AI Studio](https://aistudio.google.com/))

### Setup

```bash
git clone <repo-url> && cd couples-bot
uv sync

cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY

uv run python -m couples_bot
```

### Docker

```bash
cp .env.example .env
# Edit .env

docker compose up -d
docker compose logs -f
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for Oracle Cloud, Tailscale, Ollama, and other hosting guides.

### Web app / REST (no Telegram)

The REST adapter serves an installable web client (PWA) and a JSON API from one port. It is a drop-in replacement for Telegram for people who prefer not to use it: messages stay on your server and, with a local model, never reach a third party.

```bash
# Local, zero secrets: fake LLM, throwaway DB, two-user UI at /duo.html
uv run python dev/localtest/run_local.py --llm fake        # http://127.0.0.1:8766

# Real use, REST only (needs LLM settings; no Telegram token)
export REST_INVITE_CODE=<secret you share with your partner>
uv run python -m couples_bot --adapter rest                # http://127.0.0.1:8780

# Telegram and the web app side by side
uv run python -m couples_bot --adapter telegram+rest
```

Open the URL in a browser, register with a name (and the invite code), then pair with `/start` and `/join <code>`. To install it on a phone you need HTTPS (see [DEPLOYMENT.md](DEPLOYMENT.md#web-app-pwa--rest-api)). Scripts and AI agents can drive the same server over HTTP: see [docs/REST_API.md](docs/REST_API.md). The scripted dev rig (`uv run python dev/localtest/run_all.py`) runs every flow end to end against the fake LLM and prints `FLAGS` for anything that broke.

## Architecture

```
Transport (Telegram / REST+PWA / WebSocket / CLI) ← swappable adapter
    ↓
BotService (handlers, business logic)            ← transport-agnostic
    ↓
Prompt Assembly (persona + voice + context)      ← template-based, file-driven
    ↓
LLM Client (Gemini / OpenAI-compat / Ollama)    ← provider-agnostic
    ↓
DB (SQLite + FTS5)                               ← storage layer
    ↓
Config (YAML + env vars)                         ← deployment-agnostic
```

### Module Map

| Module | Purpose |
|--------|---------|
| `couples_bot/db.py` | SQLite schema, migrations, all CRUD functions |
| `couples_bot/search.py` | FTS5 virtual table, full-text search, index management |
| `couples_bot/prompts.py` | Prompt assembly (pack text + user context → LLM input) |
| `couples_bot/pack.py` | Prompt pack loading, validation, hot reload, snapshots |
| `couples_bot/llm.py` | LLM client protocol, Gemini + OpenAI-compat implementations |
| `couples_bot/config.py` | Frozen Config dataclass, YAML + env var loading |
| `couples_bot/handlers/service.py` | BotService — main orchestrator, mixin-based |
| `couples_bot/handlers/tags.py` | Tag handlers (#keep, #send, #deep, #blunt, #now) |
| `couples_bot/handlers/commands.py` | Slash command handlers |
| `couples_bot/handlers/callbacks.py` | Inline button callback handlers |
| `couples_bot/handlers/models.py` | BotResponse dataclass |
| `couples_bot/profile_updater.py` | Background profile/voice trait extraction from LLM output |
| `couples_bot/dampening.py` | Logarithmic dampening curves |
| `couples_bot/summarizer.py` | On-demand monthly fact summaries |
| `couples_bot/i18n.py` | Internationalization (English + Telugu) |
| `couples_bot/adapters/` | Transport adapters (Telegram, WebSocket, CLI) |
| `couples_bot/__main__.py` | Thin launcher — picks adapter from config/CLI args |
| `couples_bot/dashboard/` | Standalone admin web dashboard (stats, SQLite explorer, backup/clear actions); separate process |

### Prompt Assembly

Every word the model reads lives in a prompt pack (`packs/default/` by default, chosen with `prompt_pack` / `PROMPT_PACK`). The engine owns structure (block order, separators, which data may appear in whose prompt); the pack owns wording. Edits to pack files apply on the next message without a restart.
```
packs/default/
├── pack.yaml               ← name, version, engine_contract, file map, budgets
├── persona.md              ← core personality
├── contract.md             ← explains the JSON output to the model
├── blocks/                 ← role, not_therapy, tone
├── strings.yaml            ← every other model-visible sentence and label
├── voices/                 ← casual_en, telugu, hindi, formal_en
├── tasks/                  ← nudge, appreciation, summarize, therapy_export
├── eval/cases.yaml         ← evaluation cases
└── unloaded/               ← legacy reference text, never loaded
```
Authoring guide: [docs/PACKS.md](docs/PACKS.md).

Each LLM call is traced: a `llm_call` log line (task, model, pack name@version, pack hash, prompt hash) and a row in `llm_calls` joined to `pack_snapshots` (sha256 of every loaded pack file). No prompt text and no user id are stored.

### Evaluate a pack

`dev/prompt-eval/run_eval.py` runs the pack's `eval/cases.yaml` through the real engine over the REST API and saves replies, LLM calls and new memory rows per pack version. It does not judge; a person reads `summary.md`.

```bash
uv run python dev/prompt-eval/run_eval.py --llm fake      # plumbing check, no key needed
uv run python dev/prompt-eval/run_eval.py --llm gemini    # real model, throttled for the free tier
```

Details, result layout and quota behaviour: [docs/PACKS.md](docs/PACKS.md). `dev/localtest/run_local.py --llm gemini` starts the local rig on real Gemini (Telegram is never started).

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Get a pairing code |
| `/join <code>` | Pair with your partner |
| `/help` | Show all commands |
| `/lang <en\|te>` | Switch language (English or Telugu) |
| `/status` | Relationship dashboard |
| `/tell <message>` | Draft a gentle message for your partner |
| `/mediate [window]` | Neutral perspective from shared facts and partner impressions of the last `week` (default), `month`, `14d`, `2w` |
| `/pause` / `/resume` | Pause or resume relay delivery |
| `/history` | View recent sent and received messages |
| `/conflicts` | View open discussion topics |
| `/timeline` | View your relationship timeline |
| `/whatdoyouknow` | See what the bot remembers about you; each fact has a 🔓 Share / 🔒 Make private button |
| `/forget <id\|all>` | Delete your facts |
| `/export` | Download your facts as a file |
| `/export therapy` | Download a therapy-ready report |
| `/remember` | Revisit a positive shared memory |
| `/search <query>` | Full-text search across your facts |
| `/goal <text>` | Set a personal goal |
| `/together <text>` | Set a shared goal with your partner |
| `/goals` | View your active goals |
| `/settings` | Open settings menu (inline buttons) |
| `/vibe` | View your learned voice profile |
| `/style` | Alias for `/vibe` |
| `/mood <mood>` | Set mood (stressed, happy, angry, reflective) |
| `/setup` | Configure Gemini API keys (BYO key) |
| `/skip` | Skip onboarding questions |

## Tags

Use these in regular messages (no slash needed):

| Tag | Effect |
|-----|--------|
| `#keep` | Save as a fact, no relay to partner |
| `#send` | Draft a relay for your partner |
| `#deep` | Use a more thoughtful (heavy) model |
| `#blunt` | Get direct, honest feedback — bypasses dampening |
| `#now` | Skip cooldown on an approved relay |
| `#honest` | Alias for `#blunt` |

## Configuration Reference

All tunables live in `settings.yaml`. Every setting can be overridden via environment variables.

### Core

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Prompt pack | `prompt_pack` | `PROMPT_PACK` | `packs/default` |
| Default voice | `default_voice` | `DEFAULT_VOICE` | `casual_en` |
| Voice intensity | `default_voice_intensity` | `DEFAULT_VOICE_INTENSITY` | `0.5` |
| Database path | — | `DB_PATH` | `couples_bot.db` |
| Encryption key | — | `DB_ENCRYPTION_KEY` | — |

### LLM

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Provider | — | `LLM_PROVIDER` | `gemini` |
| Default model | `models.default` | `MODEL_DEFAULT` | `gemini-3.1-flash-lite` |
| Heavy models | `models.heavy` | `MODEL_HEAVY` | `gemini-3-flash-preview,gemini-3.8-flash` |
| Heavy daily cap | `heavy_daily_cap` | `HEAVY_DAILY_CAP` | `10` |
| OpenAI-compat base URL | — | `LLM_BASE_URL` | — |
| OpenAI-compat API key | — | `LLM_API_KEY` | — |

### Delivery & Rate Limiting

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Cooldown minutes | `cooldown_minutes` | `COOLDOWN_MINUTES` | `15` |
| Cooldown behavior | `cooldown.behavior` | `COOLDOWN_BEHAVIOR` | `accumulate` |
| Review before send | `review_before_send` | `REVIEW_BEFORE_SEND` | `true` |
| Delivery check interval | `delivery_check_interval_seconds` | `DELIVERY_CHECK_INTERVAL_SECONDS` | `30` |
| Rate limit per minute | `rate_limit_per_minute` | `RATE_LIMIT_PER_MINUTE` | `10` |
| Rate limit per day | `rate_limit_per_day` | `RATE_LIMIT_PER_DAY` | `200` |

### Backup

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Enabled | `backup.enabled` | `BACKUP_ENABLED` | `true` |
| Directory | `backup.directory` | `BACKUP_DIRECTORY` | `backups` |
| Daily count | `backup.daily_count` | `BACKUP_DAILY_COUNT` | `7` |
| Weekly count | `backup.weekly_count` | `BACKUP_WEEKLY_COUNT` | `4` |

### Digest & Check-in

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Digest enabled | `digest.enabled` | `DIGEST_ENABLED` | `true` |
| Digest time | `digest.time` | `DIGEST_TIME` | `20:00` |
| Digest timezone | `digest.timezone` | `DIGEST_TIMEZONE` | `Asia/Kolkata` |
| Check-in enabled | `checkin.enabled` | `CHECKIN_ENABLED` | `true` |
| Check-in day | `checkin.day` | `CHECKIN_DAY` | `sunday` |
| Check-in time | `checkin.time` | `CHECKIN_TIME` | `18:00` |

### Nudges & Appreciation

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Nudges enabled | `nudges.enabled` | `NUDGES_ENABLED` | `true` |
| Nudge interval (hours) | `nudges.interval_hours` | `NUDGES_INTERVAL_HOURS` | `24` |
| Quiet window | `nudges.quiet_start` / `quiet_end` | `NUDGES_QUIET_START/END` | `09:00–21:00` |
| Appreciation enabled | `appreciation.enabled` | `APPRECIATION_ENABLED` | `true` |
| Target ratio (Gottman 5:1) | `appreciation.target_ratio` | `APPRECIATION_TARGET_RATIO` | `5.0` |

### Mood & Dampening

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Mood expiry (hours) | `mood.default_expiry_hours` | `MOOD_DEFAULT_EXPIRY_HOURS` | `2` |
| Dampening enabled | `dampening.enabled` | `DAMPENING_ENABLED` | `true` |
| Dampening base | `dampening.default_base` | `DAMPENING_DEFAULT_BASE` | `0.7` |
| Blunt bypass | `dampening.blunt_bypass` | `DAMPENING_BLUNT_BYPASS` | `true` |

### FTS5 Search & Context Window

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| FTS threshold | `search.fts_threshold` | `SEARCH_FTS_THRESHOLD` | `100` |
| Recent days | `context.recent_days` | `CONTEXT_RECENT_DAYS` | `7` |
| Max relevant facts | `context.max_relevant_facts` | `CONTEXT_MAX_RELEVANT_FACTS` | `20` |

When fact count exceeds the FTS threshold, prompt building switches from including all facts to using FTS5 to pull relevant facts + recent 7-day facts + compressed monthly summaries.

### Voice Auto-Adaptation

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Auto learn | `voice.auto_learn` | `VOICE_AUTO_LEARN` | `true` |
| Learn after N messages | `voice.learn_after_messages` | `VOICE_LEARN_AFTER_MESSAGES` | `10` |

After enough messages, the bot learns your writing style (register, sentence length, emoji use, humor, directness) and adapts its responses to match.

### Adapter & Conflicts & Feedback

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Adapter | `adapter` | `ADAPTER` | `telegram` |
| WebSocket port | `ws_port` | `WS_PORT` | `8080` |
| REST host | `rest.host` | `REST_HOST` | `127.0.0.1` |
| REST port | `rest.port` | `REST_PORT` | `8780` |
| REST registration | `rest.registration` | `REST_REGISTRATION` | `invite` (`closed`, `invite`, `open`) |
| REST invite code | secret, env only | `REST_INVITE_CODE` | unset |
| REST text limit | `rest.max_text_chars` | `REST_MAX_TEXT_CHARS` | `4000` |
| REST event retention (days) | `rest.event_retention_days` | `REST_EVENT_RETENTION_DAYS` | `14` |
| REST long-poll max (s) | `rest.long_poll_max_seconds` | `REST_LONG_POLL_MAX_SECONDS` | `25` |
| Digest poll (s) | `loops.digest_poll_seconds` | `LOOPS_DIGEST_POLL_SECONDS` | `60` |
| Check-in poll (s) | `loops.checkin_poll_seconds` | `LOOPS_CHECKIN_POLL_SECONDS` | `60` |
| Nudge interval (s, 0 = `nudges.interval_hours`) | `loops.nudge_interval_seconds` | `LOOPS_NUDGE_INTERVAL_SECONDS` | `0` |
| Backup interval (s) | `loops.backup_interval_seconds` | `LOOPS_BACKUP_INTERVAL_SECONDS` | `3600` |
| Mediate default / max days | `mediate.default_days` / `mediate.max_days` | `MEDIATE_DEFAULT_DAYS` / `MEDIATE_MAX_DAYS` | `7` / `90` |
| Mediate uses impressions | `mediate.include_perceptions` | `MEDIATE_INCLUDE_PERCEPTIONS` | `true` |
| Mediate min confidence | `mediate.min_confidence` | `MEDIATE_MIN_CONFIDENCE` | `0.4` |
| Mediate themes per side | `mediate.max_themes_per_side` | `MEDIATE_MAX_THEMES_PER_SIDE` | `8` |
| Conflicts enabled | `conflicts.enabled` | `CONFLICTS_ENABLED` | `true` |
| Feedback enabled | `feedback.enabled` | `FEEDBACK_ENABLED` | `true` |
| Timeline enabled | `timeline.enabled` | `TIMELINE_ENABLED` | `true` |

## Admin Dashboard

A small web UI for the owner. It is a separate process that reads the bot's
SQLite file; it does not import the bot, the adapters, or the LLM clients, and
it never loads `.env`. Stdlib only, no extra dependencies.

**Shows:** users, messages per day, per-user activity, LLM calls per model per
day (`usage` table), pending relays and batches, conflicts, goals, and a
timeline per user and per couple. **SQLite explorer:** list tables, schema,
paged rows, read-only (no SQL box, opened `mode=ro`). **Admin actions:** backup
now, clear one user, clear the whole database. Clear actions need a typed
confirmation (`CLEAR USER <id>` / `CLEAR ALL DATA`), take a verified backup
first (aborting if it fails), and write a content-free line to
`<backup dir>/admin_audit.log`.

```bash
export DASHBOARD_TOKEN="$(openssl rand -hex 24)"   # 16+ chars, env only
DB_PATH=couples_bot.db uv run python -m couples_bot.dashboard
# open http://127.0.0.1:8765 and paste the token (or send Authorization: Bearer <token>)
```

| Setting | YAML | Env Var | Default |
|---------|------|---------|---------|
| Access token | — (env only) | `DASHBOARD_TOKEN` | required, 16+ chars |
| Bind address | `dashboard.host` | `DASHBOARD_HOST` | `127.0.0.1` |
| Port | `dashboard.port` | `DASHBOARD_PORT` | `8765` |
| Show raw message/fact text | `dashboard.show_raw_text` | `DASHBOARD_SHOW_RAW_TEXT` | `true` |
| Enable backup/clear actions | `dashboard.admin_actions_enabled` | `DASHBOARD_ADMIN_ACTIONS` | `true` |
| Explorer page size (max 200) | `dashboard.page_size` | `DASHBOARD_PAGE_SIZE` | `50` |
| Session lifetime (hours) | `dashboard.session_ttl_hours` | `DASHBOARD_SESSION_TTL_HOURS` | `12` |

The DB path and backup directory reuse `DB_PATH` and `backup.directory`.
CLI flags `--host`, `--port`, `--db`, `--settings` override them.

**Security notes**
- Bind to a LAN or Tailscale address only; the token is the sole protection and
  there is no TLS. Five failed logins from one IP lock it out for 5 minutes.
- Auth tokens (`auth_tokens.token`), encrypted API keys (`couple_config` values)
  and any column named like `token|secret|password|api_key` are redacted inside
  the SQL query and never leave SQLite. Nothing from `.env` is read.
- With `show_raw_text: false` every page shows counts and metadata only. Columns
  not classified in `dashboard/redaction.py` are hidden by default.

## Local LLM Setup (Ollama)

Run fully private with no data leaving your machine:

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3.1

# Configure pAIrtner to use it
# In .env:
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL_DEFAULT=llama3.1
```

Any OpenAI-compatible API works (Groq, Together, LM Studio, vLLM, etc.) — just set `LLM_BASE_URL` and `LLM_API_KEY`.

## Privacy Model

### What data is stored locally (SQLite)

- User profiles (relationship metadata, voice preferences, learned traits)
- Facts (user-entered notes, categorized and timestamped)
- Pending relays (softened drafts awaiting approval)
- Message history (sent/received relay log)
- Auth tokens (for the WebSocket and REST adapters)
- REST outbox: messages queued for web-app users, pruned after `rest.event_retention_days`
- Goals, timeline events, conflict tracking
- Monthly fact summaries (compressed older facts)

### What the LLM sees per request

- The persona prompt + voice instructions
- Your facts (private + shared, filtered by relevance via FTS5 when large)
- For `/mediate`: only facts both partners explicitly shared in the window, plus filtered, unverified impressions each partner's chat produced about the other. No raw messages and no private facts
- Partner themes summary (never partner's raw messages or private facts)
- Your current message
- Relationship metadata (type, duration, goals)

### What the LLM never sees

- Your partner's raw messages
- Your partner's private facts
- Auth tokens or API keys
- Other couples' data

### Partner isolation

Enforced at the SQL query level. Every fact query filters by `owner_id`. Facts are private until their owner presses the Share button in `/whatdoyouknow`; the model cannot publish a fact. Partner summaries are theme-based abstractions, not raw data.

## Adapters

pAIrtner supports multiple transport adapters:

```bash
# Default: Telegram
uv run python -m couples_bot

# WebSocket server (for custom chat apps)
uv run python -m couples_bot --adapter websocket --port 8080

# CLI (two-user terminal REPL for testing)
uv run python -m couples_bot --adapter cli

# Both Telegram + WebSocket concurrently (known limitation: each runs its own background loops)
uv run python -m couples_bot --adapter both

# REST API + web app (PWA), served from one port
uv run python -m couples_bot --adapter rest

# Telegram + REST/PWA; one shared set of background loops routes each message to the right transport
uv run python -m couples_bot --adapter telegram+rest
```

Each adapter tells users where their messages travel (the privacy note in `/start` and `/help` names the transport and the LLM in use). REST API reference: [docs/REST_API.md](docs/REST_API.md).

## Testing

```bash
uv run python -m pytest --tb=short -q      # unit and integration tests (1000+; the JS client tests need node)
uv run python dev/localtest/run_all.py     # scripted two-user flows against a fake LLM; expects "FLAGS" then "- none"
```

The dev rig (`dev/localtest/`) starts the real bot with the REST adapter, a throwaway DB and a fake LLM that upper-cases input, so flows are tested without Telegram, Gemini or a local model. `run_local.py --llm ollama` uses a real local model instead; it is for flow checks only.

## License

Private project.
