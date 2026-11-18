# pAIrtner — Feature Tracker

Project: 18k source lines, 1229 tests, 76 Python files, 87 commits.

## ✅ Done

### Infrastructure
- [x] **Transport-agnostic BotService** — Telegram, REST+PWA, WebSocket, CLI adapters
- [x] **Multi-adapter** — Telegram + REST in one process
- [x] **SQLite storage layer** — 27 tables, fully async (aiosqlite)
- [x] **Storage package** — 12 submodules, db.py is 130-line re-export facade
- [x] **Module structure** — `core/` (11 files), `features/` (8), `handlers/`, `adapters/`, `storage/`, `rest/`, `dashboard/`
- [x] **LLM providers** — Gemini + any OpenAI-compatible (Ollama, Groq, OpenRouter)
- [x] **Per-couple API keys** — encrypted, per-user override
- [x] **Config system** — YAML + env vars + runtime settings (DB-backed)
- [x] **i18n string catalog** — 234 keys, centralized (English only)
- [x] **Crypto** — Fernet encryption for API keys at rest

### Relay System
- [x] **Draft → Approve/Edit/Discard flow** — nothing reaches partner without sender approval
- [x] **Cooldown timer** — configurable per-couple
- [x] **Batch accumulation** — multiple messages within cooldown window batched
- [x] **Delivery loop** — background async, delivers when cooldown expires
- [x] **Feedback** — sender asked "did that land well?" after delivery
- [x] **Acknowledgement** — receiver can mark relay as seen
- [x] **Yolo mode** — `/yolo on` auto-approves relays (LLM can still block unsafe ones)
- [x] **Mode** — mediator (3rd person) vs voice (1st person) relay style
- [x] **Relay delivery logged to receiver history** — LLM knows about partner's messages

### Memory v0 (basic)
- [x] **Facts table** — kind (core/working/episode/partner_view), status, visibility, expiry, archival
- [x] **Memory ops from LLM** — save_fact, pin_core, update, archive, propose_core
- [x] **Op validation** — engine validates LLM proposals, deduplicates exact matches
- [x] **Version tracking** — memory_versions table, every change recorded
- [x] **Audit log** — memory_op_log table, every op logged with outcome
- [x] **Aging** — transient auto-archive 14d, situational 30d (needs user confirm)
- [x] **FTS5 search** — full-text search when fact count > threshold
- [x] **Context builder** — kind-priority ranking (core→episode→working), word-budget caps from pack
- [x] **Profile summary in context** — relationship metadata always in LLM prompt
- [x] **Fact summaries** — monthly compression table (stub)
- [x] **Partner perceptions** — themes about partner, confidence scores
- [x] **User patterns** — communication patterns, horsemen detection, frequency tracking
- [x] **Interaction scores** — Gottman 5:1 ratio tracking

### Commands (20 active)
- [x] `/start`, `/join` — pairing
- [x] `/tell`, `/mediate` — core relay + mediation
- [x] `/whatdoyouknow`, `/review`, `/search`, `/forget`, `/remember` — memory mgmt
- [x] `/style` — unified: voice profile + mood + trait overrides
- [x] `/goal` — unified: personal + shared goals, list
- [x] `/settings`, `/mode`, `/yolo`, `/lang`, `/setup` — config
- [x] `/status`, `/usage`, `/help`, `/export`, `/history` — info
- [x] `/pause`, `/resume` — relay control
- [x] `/conflicts`, `/timeline` — advanced

### PWA
- [x] **Installable PWA** — service worker, offline cache, manifest
- [x] **Chat UI** — messages, typing indicator, auto-resize textarea
- [x] **Shift+Enter** — multiline input
- [x] **Timestamps + date dividers** — "Today", "Yesterday", date headers
- [x] **Color-coded messages** — green (facts), amber (tips), purple (relay), gray (normal)
- [x] **Long-press delete** — strikethrough + grey-out, syncs to server
- [x] **LLM model name in header**
- [x] **Offline badge**
- [x] **Duo mode** — side-by-side testing (duo.html)

### Admin Dashboard
- [x] **Stats** — usage, couples, messages per day
- [x] **Read-only SQLite explorer** — browse any table safely
- [x] **Conversation viewer** — interleaved Alice+Bob view
- [x] **Runtime config editor** — change settings without restart
- [x] **Auto-generated dev token** — no crash when DASHBOARD_TOKEN unset

### Onboarding
- [x] **Single free-text prompt** — no rigid questionnaire
- [x] **LLM extraction** — structured JSON (relationship, duration, kids, age, appreciates, wish)
- [x] **Regex fallback** — works when LLM is down
- [x] **Onboarding → core facts** — profile data saved as memory

### Prompt Packs
- [x] **Pack loader** — reads from `packs/<name>/`
- [x] **Hot reload** — changed md files re-read on next call
- [x] **Per-call logging** — pack name, version, file hashes traced
- [x] **Pack template** — `packs/template/` for custom packs
- [x] **Eval runner** — test cases against real LLM, baselines

### Features
- [x] **Dampening** — configurable message softening (0.1–1.0)
- [x] **Conflict detection** — tracks open conflicts per couple
- [x] **Timeline events** — paired, conflict, resolution, milestone, goal, relay, checkin
- [x] **Goals** — personal + shared, achieve/drop callbacks
- [x] **Check-ins** — weekly check-in flow (stub, needs LLM summary)
- [x] **Digests** — daily/weekly summary (stub)
- [x] **Nudges** — appreciation prompts (stub)
- [x] **Therapy export** — downloadable report
- [x] **Backup** — JSON export of all user data
- [x] **Rate limiting** — per-user, per-window
- [x] **Crisis detection** — safety-first, resources shown

### Dev Tooling
- [x] **1229 tests** — 75 test files, 18k lines
- [x] **Fake LLM** — deterministic, no API key needed
- [x] **Local rig** — `run_local.py --llm ollama|fake|gemini --keep`
- [x] **Scenarios** — scripted two-user test flows
- [x] **Prompt eval** — eval_lib + golden prompt tests

### Deployment
- [x] **Docker + docker-compose** — server + dashboard profiles
- [x] **OPERATIONS.md** — standalone ops guide
- [x] **GitHub Pages demo** — Alice+Bob duo, Gemini API, localStorage memory
- [x] **Git clean** — no Nielsen refs, no creds in history, correct email

---

## 🔲 Pending

### P0 — Memory Engine v1 (the core)
- [x] **Recency scoring** — exponential decay: score = relevance × e^(-age/halflife). Core exempt, working facts fade.
- [x] **Source tracking** — add source_message_id to facts. Enables "why do you think that?"
- [ ] **Semantic dedup** — on save, FTS5 near-duplicate check. LLM decides merge/keep/supersede.
- [ ] **Contradiction detection** — "likes pizza" then "hates pizza" → flag, ask user.
- [ ] **Lifecycle pipeline** — new → working → (auto-promote if cited 3x) → core. Auto-archive uncited 30d.
- [ ] **Consolidation v2** — group related working facts → episode summary. Originals archived, episode kept.
- [x] **Smart context window** — ranked retrieval: core (always) + top-N working by recency×relevance + episodes.
- [x] **Aging scheduler** — wire aging/consolidation to background loop (hourly aging, weekly consolidation).
- [x] **Memory API** — structured JSON endpoints for CRUD: list, get, update, delete, search, recency-check. LLM and PWA both use it.

### P1 — Memory UX
- [ ] **PWA memory panel** — view facts, edit, delete, promote to core, see recency
- [x] **"Why do you think that?"** — trace fact back to source message (provenance API)
- [ ] **Fact rewrite** — `/review rewrite` sends facts to heavy model for cleanup
- [ ] **Inline fact editing** — tap a fact in PWA to edit its text
- [x] **Memory health indicator** — recency report API: healthy/aging/stale/contradictions

### P2 — Pack System v1
- [ ] **Move remaining hardcoded prompts** — chat, mediate, tell prompts still in prompts.py
- [ ] **Pack validation CLI** — `uv run python -m pairtner.pack validate packs/default`
- [ ] **Pack diff** — compare two pack versions, show what changed
- [ ] **Custom packs** — documentation + example for community packs

### P3 — Polish
- [ ] **LLM module split** — 453 lines → llm/ package (gemini.py, openai_compat.py, base.py)
- [ ] **User model tier** — per-user model preference in /settings
- [ ] **Check-in summary** — wire the LLM summarizer to the check-in flow
- [ ] **Digest v2** — actually useful daily/weekly summary
- [ ] **Nudge v2** — context-aware appreciation prompts (not random)

### P4 — Growth
- [ ] **GitHub Pages improvements** — better landing page, instructions
- [ ] **Multi-language** — proper i18n framework if needed (not Telugu hardcoded)
- [ ] **Circles** — any relationship (parent-child, siblings, friends)
- [ ] **Postgres option** — for multi-tenant hosting
- [ ] **Voice messages** — transcription → memory pipeline

---

## Architecture Reference

```
pairtner/
├── adapters/        # Telegram, REST, WebSocket, CLI, background loops
├── core/            # memory, onboarding, prompts, analysis, consolidation, search, dampening
├── features/        # approval, checkin, conflict, digest, nudges, therapy export, feedback
├── handlers/        # dispatch, commands, callbacks, tags, service (BotService)
├── storage/         # 12 submodules: users, messages, facts, pending, profiles, couples, goals, usage, outbox, context, settings, helpers
├── dashboard/       # admin web UI
├── rest/            # REST API layer
├── web/             # PWA static files
├── db.py            # re-export facade (130 lines)
├── llm.py           # LLM clients (Gemini + OpenAI-compat)
├── config.py        # Config dataclass
├── i18n.py          # String catalog (496 lines, EN only)
├── pack.py          # Pack loader
└── crypto.py        # Fernet encryption
```
