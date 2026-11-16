# pAIrtner — Roadmap & System Design

## Architecture Principles

1. **Decoupled layers** — Transport, handlers, LLM, DB, config are independent. Swap any without touching others.
2. **Data is gold** — Store raw data always. LLMs are replaceable; the schema and history are the product.
3. **Privacy structural** — Partner data isolation enforced at SQL level, not prompt instructions.
4. **Culturally adaptive** — No hardcoded Western therapy-speak. Match the user's natural register.
5. **Progressively useful** — Bot becomes less necessary over time as users learn to communicate better.

## System Layers

```
Transport (Telegram / WebSocket / REST)         ← swappable adapter
    ↓
BotService (handlers, business logic)            ← transport-agnostic
    ↓
Prompt Assembly (persona + voice + context)      ← template-based, file-driven
    ↓
LLM Client (Gemini / OpenAI-compat / Ollama)    ← provider-agnostic
    ↓
DB (SQLite / Postgres)                           ← storage-agnostic
    ↓
Config (YAML + env vars)                         ← deployment-agnostic
```

## Prompt Assembly Architecture

Each LLM call assembles from building blocks:

```
┌─────────────────────────────────────┐
│ System Instruction                  │
│  persona.md (core personality)      │
│  + voices/{style}.md (cultural)     │
│  + task prompt (rewrite/mediate/...) │
│  + user style profile               │
├─────────────────────────────────────┤
│ Context                             │
│  User's facts (filtered)            │
│  User's recent messages (last N)    │
│  Partner summary (themes, NOT raw)  │
│  Shared facts                       │
│  Active goals                       │
│  Relationship metadata              │
├─────────────────────────────────────┤
│ Current message (sanitized)         │
├─────────────────────────────────────┤
│ Output: JSON                        │
│ {reply, new_facts, relay_draft,     │
│  profile_updates,                   │
│  articulation_note}                 │
└─────────────────────────────────────┘
```

### Prompt files

```
prompts/
├── persona.md              ← who the bot is (editable, loaded at startup)
├── voices/
│   ├── casual_en.md        ← default relaxed English
│   ├── telugu.md           ← Telugu/Tenglish style
│   ├── hindi.md            ← Hindi style
│   └── formal_en.md        ← therapy-style English
├── tasks/
│   ├── rewrite.md          ← tone rewriting instructions
│   ├── mediate.md          ← mediation prompt
│   ├── digest.md           ← daily digest generation
│   ├── checkin.md          ← weekly check-in
│   ├── articulate.md       ← articulation teaching rules
│   └── summarize.md        ← fact/pattern summarization
└── templates/
    ├── user_context.md     ← template for user context block
    └── partner_summary.md  ← template for partner themes
```

## Data Model Evolution

### Phase 1 (done) — Core tables
- users, facts, pending, messages, usage
- couple_config (BYO keys, encrypted)
- pending_batch, pending_batch_items (cooldown accumulation)
- rate_limit
- checkin_sessions

### Phase 2 (batch 3) — Intelligence tables

```sql
-- User communication profile (auto-updated every message)
user_profiles (
    user_id             INTEGER PRIMARY KEY,
    default_voice       TEXT DEFAULT 'casual_en',
    voice_intensity     REAL DEFAULT 0.5,       -- 0.0 pure EN → 1.0 deep regional
    dampening_base      REAL DEFAULT 0.5,       -- 0.0 raw → 1.0 max soft
    onboarding_done     BOOLEAN DEFAULT FALSE,
    relationship_type   TEXT,                    -- love / arranged / other
    duration_years      INTEGER,
    has_kids            BOOLEAN,
    age_range           TEXT,
    love_story_brief    TEXT,
    updated_at          TIMESTAMP
)

-- Mood-specific style overrides
user_mood_styles (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER,
    mood        TEXT,           -- stressed / happy / angry / reflective / default
    voice       TEXT,           -- override voice for this mood
    dampening   REAL,           -- override dampening
    notes       TEXT,           -- "be brief, no questions"
    created_at  TIMESTAMP
)

-- Communication patterns (auto-detected by LLM)
user_patterns (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER,
    pattern_type    TEXT,       -- communication / emotional / conflict / growth
    pattern         TEXT,       -- "uses absolutes", "avoids requests"
    frequency       INTEGER,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP,
    status          TEXT DEFAULT 'active'  -- active / improving / resolved
)

-- What user perceives/knows about partner (auto-maintained)
partner_perceptions (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER,
    partner_id      INTEGER,
    theme           TEXT,       -- "values quality time"
    sentiment       TEXT,       -- positive / negative / neutral
    confidence      REAL,
    source_count    INTEGER,
    last_updated    TIMESTAMP
)

-- Personal growth goals
user_goals (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER,
    goal        TEXT,
    status      TEXT DEFAULT 'active',
    progress    TEXT,           -- JSON: tracked instances
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP
)

-- Shared couple goals
shared_goals (
    id          INTEGER PRIMARY KEY,
    couple_id   TEXT,           -- "{user1_id}:{user2_id}"
    goal        TEXT,
    status      TEXT DEFAULT 'active',
    progress    TEXT,
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP
)

-- Conflict tracking
conflicts (
    id              INTEGER PRIMARY KEY,
    couple_id       TEXT,
    theme           TEXT,
    status          TEXT DEFAULT 'open',  -- open / resolved / recurring
    relay_ids       TEXT,                 -- JSON: related pending IDs
    opened_at       TIMESTAMP,
    resolved_at     TIMESTAMP
)

-- Relationship timeline events
timeline_events (
    id          INTEGER PRIMARY KEY,
    couple_id   TEXT,
    event_type  TEXT,           -- paired / conflict / resolution / milestone / goal
    summary     TEXT,
    metadata    TEXT,           -- JSON
    created_at  TIMESTAMP
)
```

### Phase 3 — Search & retrieval
- FTS5 virtual table on facts.text for keyword search
- Embedding-based similarity (future, needs vector storage)
- Gemini function calling: search_facts(query), get_recent_conflicts(), etc.

## Feature Roadmap

### Batch 3 — Intelligence Layer
- [ ] User profiles table + onboarding interview
- [ ] Cultural voice presets (spectrum, not buckets) + /style command
- [ ] Mood detection (auto + /mood) with style adaptation
- [ ] Fact lifecycle (fresh → active → aging → archived) + contradiction detection
- [ ] Partner summaries (themes, not raw text) in prompts — privacy model upgrade
- [ ] Articulation teaching (show rewrites + explain why + track progress)
- [ ] Logarithmic dampening curves (per-user, per-mood, over-time fading)
- [ ] Goal tracking (/goal personal, /together shared)
- [ ] Continuous profile auto-updates via LLM output
- [ ] /settings command (chat-based edit flow with inline buttons)
- [ ] Per-partner Gemini key option (three-tier lookup)
- [ ] Prompts reorganization: prompts/ directory with voices/ and tasks/

### Batch 4 — Engagement & Growth
- [ ] /remember + proactive positive nudges
- [ ] Conflict resolution tracking
- [ ] Feedback loop on relays ("did that land well?")
- [ ] A/B rewrite options (two versions, user picks, bot learns)
- [ ] Appreciation prompts (proactive 5:1 ratio nudges)
- [ ] Relationship timeline (/timeline)
- [ ] Read receipts (soft — "received ✓", not timestamps)
- [ ] Emergency detection (abuse/crisis → helpline immediately)
- [ ] Shared goals (/together)
- [ ] /export structured (for therapy — timeline, themes, patterns)

### Batch 5 — Scale & Platform
- [ ] FTS5 full-text search on facts
- [ ] Smart context window (7-day full + older summaries)
- [ ] Gemini function calling (search_facts tool)
- [ ] Own chat app (WebSocket adapter, Tailscale hosting)
- [ ] Postgres option for multi-tenant
- [ ] Oracle Cloud / VM deployment guide
- [ ] Analytics dashboard

## Design Patterns

### Gottman Framework (internal, not user-facing)
The bot uses Gottman's research as its analytical lens:
- **Four Horsemen detection**: criticism, contempt, defensiveness, stonewalling in messages
- **Repair attempt suggestions**: de-escalation bids after conflict
- **5:1 ratio tracking**: positive vs negative interaction count, nudge when low
- **Bids for connection**: encourage and track small positive moments

### Dampening Curve
```
Input intensity → Output intensity (logarithmic)

Harsh (10) → Gentle (4)      ← heavy dampening
Firm  (6)  → Softer (4)      ← moderate dampening  
Mild  (3)  → Slightly (2.5)  ← barely touched
Kind  (1)  → Kind (1)        ← no change

Curve shifts per:
- User's dampening_base setting
- Detected mood (stressed → more dampening)
- Time (month 1: heavy, month 6: light — user is learning)
- Explicit #blunt tag: bypass dampening entirely
```

### Fact Lifecycle
```
New fact → Fresh (0-7 days, always in prompt)
        → Active (7-30 days, in prompt)
        → Aging (30-90 days, ask "still relevant?" or auto-demote)
        → Archived (90+ days, compressed into monthly summary)
        → Retrieved (pulled back by search when relevant)

Contradiction: new fact conflicts with existing
  → Ask user: "This seems different from before. Has something changed?"
  → Update or archive the old one
  → Log the change as a growth signal
```

### Multi-call Retrieval (when KB grows large)
```
Call 1 (lightweight, Flash-Lite):
  Input: user message + recent 5 messages
  Output: {search_queries: ["kitchen chores", "work stress"]}

→ FTS5 search → relevant facts

Call 2 (normal):
  Input: persona + voice + relevant facts + partner summary + message
  Output: {reply, new_facts, relay_draft, profile_updates}
```

## Hosting Options

| Option | Cost | Best for |
|--------|------|----------|
| Local machine | Free | Development, testing |
| Docker on plex-stack | Free | Home always-on |
| Oracle Cloud Free Tier | Free | Production always-on (ARM, 24GB RAM) |
| Tailscale + local | Free | Own chat app, no public port |
| Fly.io | ~$3/mo | Easy Docker deploy |
| Ollama on Mac | Free | Local LLM, fully private |
