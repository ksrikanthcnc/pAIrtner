# pAIrtner — How It All Works

A complete reference for how the memory, privacy, relay, and knowledge systems fit together.

---

## The Two Tables

Everything is built on two tables:

| Table | Purpose | Mutability |
|---|---|---|
| `messages` | Raw conversation log (both user + bot messages) | **Append-only. Never modified.** |
| `knowledge` (facts) | LLM-curated facts about each user | Created, updated, archived by LLM + engine |

Messages are the immutable ground truth. Knowledge is the living, curated memory the LLM works with.

---

## Message Flow — What Happens When You Send a Message

```
User sends: "I'm stressed about the interview on Friday"
│
├─► messages table (auto-saved, role=user)
│
├─► LLM receives: system prompt + knowledge context + recent messages + your message
│
└─► LLM returns JSON:
    {
      "reply": "That sounds stressful. Tell me more about it.",
      "memory_ops": [
        {"op": "add", "text": "Stressed about Friday interview", "kind": "working", "intensity": 0.8}
      ],
      "relay_draft": "Hey, your partner mentioned feeling stressed about an upcoming interview."
    }
```

Three things happen:
1. **Reply** → sent back to you
2. **memory_ops** → facts auto-saved to knowledge table (the "1 note added" message)
3. **relay_draft** → if present, you see a "💬 Share with partner" button

---

## Fact Visibility — Private vs Shared

Every fact has a `visibility` field:

| Visibility | Who sees it | How it's set |
|---|---|---|
| `private` (default) | Only the owner | Auto — LLM extractions default to private |
| `shared` | Both partners | User explicitly shares via `fv:` toggle or `#send` |

**Privacy rule (enforced structurally, not by prompt):**
- Your prompt gets: YOUR private facts + SHARED facts from both partners
- Your prompt NEVER gets: partner's private facts
- This is verified by 8+ tests in `test_privacy.py`

### Example: Sensitive information
```
Alice tells the bot: "I wish Bob would exercise more but I can't say that"
→ Extracted as: private fact for Alice: "Wishes Bob would exercise more"
→ Bob's prompt NEVER contains this fact
→ But the LLM, when talking to Alice, might suggest:
  "Have you thought about doing something active together as a couple?"
```

---

## Tags — How Users Control What Happens

| Tag | What it does |
|---|---|
| (no tag) | Bot replies + extracts facts. If relay_draft exists, shows "Share with partner" button |
| `#keep` | Save facts only. Bot acknowledges but doesn't generate a relay draft |
| `#send` | Draft a relay message for partner. Goes through approval flow |
| `#deep` | Use the heavy model (more thoughtful reply). Same flow otherwise |
| `#blunt` | No sugarcoating. Direct, honest response |

---

## Relay Flow — Sharing Messages With Partner

The relay system lets you share thoughts with your partner through the bot,
with the bot rewriting for gentleness.

```
1. User sends message (with #send tag, or bot suggests a relay_draft)
2. Bot generates a relay_draft — a softened version of what you said
3. User sees: "💬 Share with partner" button
4. User clicks → draft goes to approval flow
5. After cool-down (~15 min, configurable), the rewritten message is
   delivered to the partner's chat
6. Partner receives it as: "Your partner wanted to share something..."
```

### Approval buttons (after #send or clicking Share):
- ✅ **Approve** — schedule for delivery after cool-down
- ✏️ **Edit** — rewrite the draft yourself, then approve
- ❌ **Discard** — delete the draft, nothing sent

The cool-down exists so you don't send something in the heat of the moment.

---

## Knowledge / Facts — What the Bot Remembers

### Fact kinds

| Kind | Description | Decay | Always in prompt? |
|---|---|---|---|
| `core` | Identity-level, durable. "Alice is 32", "Married 6 years" | Never | Yes |
| `working` | Current situations. "Stressed about interview" | 21-day halflife | Score-ranked |
| `episode` | Compressed summaries of older facts | 60-day halflife | Score-ranked |

### Fact states

| State | Meaning |
|---|---|
| `active` | Current, relevant |
| `improving` | Getting better (tracked for positive reinforcement) |
| `resolved` | Done/over. Eligible for weekly consolidation |
| `outdated` | No longer relevant. Low score, eligible for consolidation |

### How the bot decides what to remember

Every message, the LLM can return `memory_ops`:
- `add` — new fact extracted from the conversation
- `revise` — update an existing fact (e.g., "married 6 years" → "married 7 years")
- `state_change` — mark a fact as resolved/improving/outdated
- `promote` — upgrade a working fact to core (identity-level)

---

## Scoring — How the Bot Chooses What to Send to the LLM

The LLM has a limited context window (~1200 words for knowledge). The scoring
system ranks all facts and picks the top-K:

```
score = kind_weight × decay(age) × status_weight × intensity × citation_boost
```

| Factor | Effect |
|---|---|
| `kind_weight` | core=1.0, episode=0.7, working=0.5 |
| `decay(age)` | Exponential decay. Core never decays. Working halflife=21d |
| `status_weight` | active=1.0, resolved=0.3, outdated=0.1 |
| `intensity` | LLM-assigned importance (0.0-1.0) |
| `citation_boost` | Facts included in past prompts get boosted (up to 1.5x) |

Core facts always score 1.0 (always in the prompt).

### Per-message FTS5 search
On each message, the engine also runs a full-text search for relevant old facts
that might not rank high by score alone. E.g., if you mention "peanut allergy,"
an old fact about your partner's allergy surfaces even if it hasn't been cited recently.

---

## Consolidation — How Old Facts Get Compressed

### Weekly (runs every 7 days)
1. Finds resolved/outdated working facts
2. Sends them to the LLM: "Group by theme and summarize"
3. LLM returns multiple topic-grouped episodes (e.g., 20 facts → 4 episodes)
4. Source facts get archived (soft delete)

### Monthly (runs every 30 days)
1. Finds weekly episodes older than 30 days
2. LLM merges related weekly episodes into broader monthly summaries
3. Source weekly episodes get archived

### Quarterly (runs every 90 days)
1. Finds monthly episodes older than 90 days
2. LLM creates broad quarterly overviews
3. Source monthly episodes get archived

**Raw messages are NEVER archived.** They're the rebuild source.

---

## Proactive Context Injection

Some facts should be in the LLM's prompt regardless of their score:
- Approaching birthdays/anniversaries (7-day lookahead)
- Approaching deadlines
- The LLM stores dates in structured `event_date` + `recurring` columns

---

## Commands Reference

| Command | What it does |
|---|---|
| `/start` | Get a pairing code |
| `/join <code>` | Join your partner |
| `/lang <en\|te>` | Switch language |
| `/whatdoyouknow` | See all facts the bot has about you |
| `/forget <id>` | Delete a specific fact |
| `/export` | Export your data |
| `/yolo on\|off` | Auto-send relay messages (skip approval) |
| `/status` | Show your account status |

---

## Architecture

```
packs/default/           ← Prompt templates (persona, tone, task prompts)
pairtner/
  core/                  ← Memory engine, scoring, consolidation, proactive
  handlers/              ← Message routing, tag handling, callbacks
  features/              ← Approval, analysis, check-in, therapy export
  storage/               ← DB schema, facts CRUD, context building
  adapters/              ← Telegram, REST, background loops
  web/                   ← PWA (duo.html for testing)
v2/
  contracts/             ← YAML schemas (response format, scoring knobs)
  prompts/               ← MD files (extraction, consolidation prompts)
  DECISIONS.md           ← All design decisions (numbered)
dev/
  benchmark/             ← Model comparison framework
  localtest/             ← Local testing with Ollama
```


---

## Telegram-Specific Behavior

### Message Splitting
Telegram has a hard 4096-character limit per message. The adapter automatically
splits long replies at paragraph > newline > sentence > hard-cut boundaries.
Buttons (e.g., "Share with partner") attach to the **last** chunk only.

### Offline Behavior
The bot uses **long-polling** (not webhooks). When the server is down:
- Telegram **buffers undelivered messages** on their servers (up to ~24h in practice).
- When the bot reconnects, it receives any messages sent while it was offline.
- User messages are NOT lost — they're queued by Telegram.
- Bot **replies** that failed to send while offline are lost (the bot doesn't retry).
  The user just sees no response for that message.

### LAN Access (Android)
Run with `--lan` flag to bind to `0.0.0.0` instead of `127.0.0.1`:
```
uv run python dev/localtest/run_local.py --llm ollama --port 8766 --keep --lan
```
Then open `http://<mac-ip>:8766/duo.html` from any device on the same WiFi.

---

## Debug & Warnings

| Command | Level | What it shows |
|---|---|---|
| `/whatdoyouknow` | User | All active facts for the user (existing) |
| `/debug` (planned) | Full | Knowledge table state, scores, FTS5 index, metadata |
| `/warnings` (planned) | Warn | Dropped consolidation episodes, quality failures, schedule skips |

Quality checks during consolidation (min 8 words per episode, 50% coverage)
log warnings when episodes are dropped. The `/warnings` command will surface
these to the user.
