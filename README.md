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

## Architecture

```
Transport (Telegram / WebSocket / CLI)           ← swappable adapter
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
| `couples_bot/prompts.py` | Prompt building (persona + voice + context → LLM input) |
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

### Prompt Assembly

Each LLM call assembles from building blocks stored in `prompts/`:

```
prompts/
├── persona.md              ← core personality (loaded at startup)
├── voices/
│   ├── casual_en.md        ← relaxed English
│   ├── telugu.md           ← Telugu/Tenglish style
│   ├── hindi.md            ← Hindi style
│   └── formal_en.md        ← therapy-style English
├── tasks/
│   ├── rewrite.md          ← tone rewriting
│   ├── mediate.md          ← mediation
│   ├── digest.md           ← daily digest
│   ├── checkin.md          ← weekly check-in
│   ├── articulate.md       ← articulation teaching
│   └── summarize.md        ← fact summarization
└── templates/
    ├── user_context.md
    └── partner_summary.md
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Get a pairing code |
| `/join <code>` | Pair with your partner |
| `/help` | Show all commands |
| `/lang <en\|te>` | Switch language (English or Telugu) |
| `/status` | Relationship dashboard |
| `/tell <message>` | Draft a gentle message for your partner |
| `/mediate` | Get a neutral perspective on shared issues |
| `/pause` / `/resume` | Pause or resume relay delivery |
| `/history` | View recent sent and received messages |
| `/conflicts` | View open discussion topics |
| `/timeline` | View your relationship timeline |
| `/whatdoyouknow` | See what the bot remembers about you |
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
| Persona file | `persona_file` | `PERSONA_FILE` | `persona.md` |
| Prompts directory | `prompts_dir` | `PROMPTS_DIR` | `prompts` |
| Voices directory | `voices_dir` | `VOICES_DIR` | `prompts/voices` |
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
| Conflicts enabled | `conflicts.enabled` | `CONFLICTS_ENABLED` | `true` |
| Feedback enabled | `feedback.enabled` | `FEEDBACK_ENABLED` | `true` |
| Timeline enabled | `timeline.enabled` | `TIMELINE_ENABLED` | `true` |

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
- Auth tokens (for WebSocket adapter)
- Goals, timeline events, conflict tracking
- Monthly fact summaries (compressed older facts)

### What the LLM sees per request

- The persona prompt + voice instructions
- Your facts (private + shared, filtered by relevance via FTS5 when large)
- Partner themes summary (never partner's raw messages or private facts)
- Your current message
- Relationship metadata (type, duration, goals)

### What the LLM never sees

- Your partner's raw messages
- Your partner's private facts
- Auth tokens or API keys
- Other couples' data

### Partner isolation

Enforced at the SQL query level. Every fact query filters by `owner_id`. Shared facts are explicitly marked. Partner summaries are theme-based abstractions, not raw data.

## Adapters

pAIrtner supports multiple transport adapters:

```bash
# Default: Telegram
uv run python -m couples_bot

# WebSocket server (for custom chat apps)
uv run python -m couples_bot --adapter websocket --port 8080

# CLI (two-user terminal REPL for testing)
uv run python -m couples_bot --adapter cli

# Both Telegram + WebSocket concurrently
uv run python -m couples_bot --adapter both
```

## Testing

```bash
uv run python -m pytest --tb=short -q
```

## License

Private project.
