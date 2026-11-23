# pAIrtner — Full System Flow

End-to-end walkthrough of how pAIrtner works, from first launch to daily use.

## 1. Starting the Server

```bash
# Auto-detects available LLM providers (Ollama, Gemini, or both)
uv run python dev/localtest/run_local.py --keep

# Explicit provider:
uv run python dev/localtest/run_local.py --keep --llm ollama
uv run python dev/localtest/run_local.py --keep --llm gemini

# Both providers (Ollama primary, Gemini via /model prefix):
GEMINI_API_KEY=your-key uv run python dev/localtest/run_local.py --keep

# Smaller model for low-end hardware:
LOCAL_MODEL=gemma3:4b uv run python dev/localtest/run_local.py --keep --pack simple
```

On startup:
1. **Provider auto-detect** probes Ollama (localhost:11434) and checks `GEMINI_API_KEY`
2. **Prompt pack** loads from `packs/default/` (persona, contract, voices, tasks)
3. **Schema** initializes SQLite DB via `init_db()` + `migrate_to_v2()`
4. **REST adapter** starts on `http://127.0.0.1:8766`
5. **Background loops** start: delivery check, memory maintenance (consolidation, aging)

## 2. Pairing Flow (Duo)

Open `http://127.0.0.1:8766/duo_keep.html` — two side-by-side chat panels.

```
Alice: /start
  → Bot: "Your pairing code is: A1B2C3"
  → Bot shows disclaimer + crisis resources

Bob: /join A1B2C3
  → Bot: "🎉 Great, you're paired! Just chat naturally..."
  → Mentions /password for PWA access

Alice: "We've been together 5 years, 2 kids, he works too much"
  → LLM auto-extracts: relationship_type, duration, has_kids, concerns
  → Stores as core facts, marks onboarding done
  → No /skip needed — any message completes onboarding
  → Greetings ("hi", "hey") also complete it instantly

Bob: "Hey"
  → Onboarding completes (greeting detected)
  → Bot: "Hey! What's on your mind?"
  → Normal conversation mode
```

### What happens under the hood:
- `/start` → `handle_start()` → generates 6-char hex code, stores in `_pairing_codes`
- `/join` → `handle_join()` → `set_partner()`, creates `relationships` row, records timeline event
- First message → `process_onboarding_answer()` → LLM extracts structured data → stores in `user_profiles` + core facts
- Password → user types `/password mypass` anytime (mentioned in welcome, not forced)

## 3. Conversation Flow

```
User message → dispatch() → _handle_plain()
  ├── Build system prompt (persona + contract + voice + context)
  │   ├── Retrieve relevant facts (FTS5 + recency scoring)
  │   ├── Include rolling summaries (weekly → monthly → quarterly)
  │   ├── Include partner patterns (if shared visibility)
  │   └── Apply persona tuning (adaptive tone from interaction history)
  ├── Call LLM.generate()
  │   ├── Routes to Ollama or Gemini based on model name
  │   ├── Traces: pack hash, prompt SHA, model name
  │   └── Records usage count
  ├── Parse response (JSON with reply + memory_ops + settings_ops)
  │   ├── reply → shown to user
  │   ├── memory_ops → add/revise/archive/pin_core facts
  │   └── settings_ops → update user preferences
  ├── Apply memory operations
  │   ├── Semantic dedup (cosine similarity check)
  │   ├── Contradiction detection
  │   └── Store with owner_id, visibility, kind, tag
  ├── Check for relay draft (if dampening suggests one)
  ├── Log message to DB
  └── Return BotResponse
```

### What's actually in the LLM prompt

The prompt has two parts: **system prompt** and **user content**.

**System prompt** (from prompt pack markdown files):
- `persona.md` — bot personality, role, boundaries
- `contract.md` — extraction rules (what to store, what NOT to)
- Voice instructions — the user's learned communication style
- Dampening instruction — "Be gentle and diplomatic" / "Keep the directness"
- Tone overlay — persona tuning based on accumulated interaction patterns
- Goals text — the user's personal/shared goals

**User content** (assembled by `ComposedContext`):
```
1. CORE FACTS (permanent: "married 10 years", "2 kids")
2. WORKING FACTS (active, non-summarized: "stressed about deadline")
3. PARTNER'S SHARED FACTS (only visibility=shared from the other person)
4. NUDGE SUGGESTIONS (gentle guidance to weave in if natural)
5. SUMMARIES (rolling: latest weekly + monthly + quarterly narrative)
6. PROACTIVE (upcoming events, goals)
7. RECENT MESSAGES (last 6+ chat exchanges for conversational context)
8. CURRENT MESSAGE (the user's actual input)
```

Each section is word-capped to fit the context budget. The total is typically 2000-4000 words depending on how much memory exists.

## 4. Memory System (the "Knowledge" tab)

"Memory" and "Knowledge" refer to the same thing: the `facts` table. The PWA tab is called "Knowledge" but the codebase uses "memory" everywhere. Each fact is a piece of information the bot learned from your conversations.

### Kinds
| Kind | Purpose | Lifecycle |
|------|---------|-----------|
| `core` | Permanent facts (married 10 years) | Only changed by explicit user action |
| `working` | Active context (stressed about deadline) | Stays active, consolidated over time |
| `episode` | Specific events (brought flowers Tuesday) | Consolidated into summaries |
| `partner_view` | How partner sees you | Updated from partner's shared facts |

### Lifecycle
```
Message → LLM extracts facts → stored as working/episode
  ↓
Weekly consolidation → groups by tag → creates weekly summary
  ↓
Monthly consolidation → rolls up weekly summaries
  ↓
Quarterly consolidation → rolls up monthly summaries
```

- **No auto-outdate**: working facts stay active forever (consolidated, not deleted)
- **Core = explicit**: only promoted via user command, onboarding, or LLM `pin_core` op
- **Summaries** are tagged with period + source facts for provenance

### Visibility
- `private` — only the owner sees it, never in partner context
- `shared` — both partners' prompts can reference it, nudges allowed

## 5. Relay System

### Dampening
Dampening preserves the user's natural voice in relay messages. It does NOT change regular conversation — that's handled by the voice system (which learns the user's register). Dampening only applies when softening a message before sending it to the partner.

Logarithmic intensity scaling:
- **0.0** = verbatim (keep the energy and directness)
- **0.3** = light touch (preserve meaning, smooth rough edges)
- **0.7** = gentle (rephrase constructively, focus on feelings)
- **1.0** = very gentle (diplomatic, collaborative framing)

The base dampening level is per-user (adjustable via `/settings`), modified by mood.

### Relay approval is conditional

The system auto-classifies relay urgency:

| Urgency | Behavior | Example |
|---------|----------|---------|
| `info` (default) | **Auto-nudge** — no approval needed, woven into partner's next chat naturally | "tell Bob I'll be late" |
| `important` | **Approval required** — user sees [Approve/Edit/Cancel] buttons | "tell Bob the dishes situation is driving me crazy" |
| `yolo` mode | **Auto-send** — user set `relay_mode=yolo` via `/settings`, everything sends after cooldown | (all relays) |

### Relay batching (cooldown timer)
When a user approves multiple relays within the cooldown window (`cooldown_minutes`, default 15), they accumulate in a batch (`relay_batch` + `relay_batch_items`). Each new approval resets the timer. When no new activity for `cooldown_minutes`, the delivery loop merges and sends the entire batch as one message.

`cooldown_behavior` options:
- `accumulate` (default) — new relays add to the batch, timer continues from original
- `reset` — each new relay restarts the cooldown timer

### Nudges
Nudge suggestions are auto-generated from partner's shared facts. They're gentle prompts the bot weaves into conversation naturally ("Bob's been trying with flowers — maybe acknowledge it?"). Nudges are:
- Stored as facts with `nudge_status='pending'`
- Deduped by the same semantic dedup system as regular facts
- Included in the prompt as "Gentle guidance to weave in if natural"
- Never forced — the LLM decides if/when to surface them
- Not timer-batched like relays (they're guidance, not direct messages)

## 6. Commands

| Command | What it does |
|---------|-------------|
| `/start` | Generate pairing code |
| `/join <code>` | Pair with partner |
| `/forget <text>` | Delete matching facts |
| `/share <tag>` | Make facts with tag shared |
| `/private <tag>` | Make facts with tag private |
| `/search <query>` | Two-pass search (FTS5 → LLM rerank) |
| `/goal <text>` | Set a personal or shared goal |
| `/settings [key] [value]` | View/change preferences |
| `/mode <vent\|relay\|coach>` | Set interaction mode |
| `/model <name>` | Switch LLM model (supports `gemini:` / `ollama:` prefix) |
| `/model reset` | Back to server default |
| `/rewrite` | LLM rewrites all working facts (cleanup) |
| `/export therapy` | Prose export for therapy sessions |
| `/status` | Your memory stats |
| `/usage [days]` | LLM usage stats |
| `/debug <msg>` | Show raw LLM input/output |
| `/dryrun <msg>` | Test without saving to DB |
| `/warnings` | Show system health warnings |
| `/topics` | Browse facts by topic tag |
| `/history [n]` | Recent messages |
| `/password <pw>` | Set PWA login password |
| `/pause` / `/resume` | Pause/resume relay delivery |
| `/help` | Full command list |

**Tags** (in regular messages):
- `#keep` — force-save as a fact
- `#send` — draft a relay to partner
- `#blunt` — bypass dampening, get honest feedback
- `#deep` — use heavy model for this message

## 7. PWA (Web App)

Served at `http://127.0.0.1:8766/`. Tabs:

| Tab | What |
|-----|------|
| **Chat** | Real-time conversation with SSE streaming |
| **Dashboard** | Usage stats, message counts, activity |
| **Knowledge** | Browse, search, edit, delete facts (by kind, tag, visibility) |
| **Couple** | Relationship view — conflicts, nudges, timeline, facts |
| **Settings** | Preferences, sharing mode, voice, model |
| **Admin** | (admin users) Stats, SQL explorer, dry-run |

### Dev pages
- `/duo.html` — throwaway side-by-side (fresh DB each time)
- `/duo_keep.html` — persistent side-by-side (survives restart)

## 8. Multi-Provider LLM

When both Ollama and Gemini are configured, the `MultiProviderLLM` routes by model name:

```
/model qwen3:8b          → Ollama (default)
/model gemma3:4b          → Ollama
/model gemini:flash-2     → Gemini (prefix stripped)
/model gemini-3.1-flash   → Gemini (native name detected)
/model ollama:qwen3:8b    → Ollama (explicit prefix)
/model reset              → back to server default
```

The `/model` command validates the connection before saving — if Gemini isn't configured or the model doesn't exist, you get an error instead of broken replies.

## 9. Background Loops

| Loop | Interval | What |
|------|----------|------|
| **Delivery** | 30s | Check relay_queue for approved messages → deliver |
| **Memory maintenance** | 1hr | Run consolidation (weekly → monthly → quarterly), prune old episodes |

## 10. Data Model (key tables)

| Table | Purpose |
|-------|---------|
| `users` | User profiles, partner links |
| `messages` | Full chat history (user + assistant) |
| `facts` | Knowledge base (core/working/episode/partner_view) |
| `goals` | Personal and shared goals (unified table with scope) |
| `relationships` | Partner pairs with relationship metadata |
| `relay_queue` | Individual relay messages (draft → approved → sent) |
| `relay_batch` / `relay_batch_items` | Grouped relay batches |
| `relationship_config` | Per-couple settings |
| `timeline_events` | Relationship timeline (paired, conflict, resolution) |
| `conflicts` | Tracked conflict themes |
| `interaction_scores` | Positive/negative interaction tracking (Gottman ratio) |
| `user_profiles` | Onboarding data, voice preferences, learned patterns |
| `rest_outbox` | SSE event queue for real-time PWA updates |

## 11. Prompt Pack System

All bot behavior lives in markdown files:

```
packs/default/
├── persona.md           # Bot personality and role
├── contract.md          # Extraction rules (what to remember, what not to)
├── pack.yaml            # Pack metadata and config
├── strings.yaml         # UI strings (multilingual)
├── voices/              # Communication styles
│   ├── casual_en.md
│   ├── formal_en.md
│   ├── hindi.md
│   └── telugu.md
├── tasks/               # Task-specific prompts
│   ├── chat.md          # Main conversation
│   ├── nudge.md         # Proactive nudges
│   ├── summarize.md     # Summary generation
│   ├── analysis.md      # Pattern analysis
│   └── ...
└── blocks/              # Reusable prompt sections
    ├── role.md
    ├── tone.md
    └── not_therapy.md
```

**Simple pack** (`packs/simple/`) — stripped-down contract for 4B models. Use with `--pack simple`.

## 12. Stats

- **30,369** lines of Python
- **106** Python source files
- **120** test files, **1,469** test functions
- **208** commits
- **34** tables in SQLite schema
