# pAIrtner — Feature Tracker

Project: ~20k source lines, 1594 tests, 104 Python files, 184 commits.

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
- [x] **Semantic dedup** — FTS5 + SequenceMatcher near-duplicate check. 90%+ = skip, 75-89% = check contradictions.
- [x] **Contradiction detection** — heuristic same-subject opposite-sentiment check. Flags for review.
- [x] **Lifecycle pipeline** — core = explicit only (permanent). Working = stays active, ages via consolidation into summaries. No auto-outdate/promote.
- [x] **Consolidation v2** — group related working facts → episode summary. Weekly → monthly → quarterly cascade. Originals archived, episodes kept.
- [x] **Smart context window** — ranked retrieval: core (always) + top-N working by recency×relevance + episodes.
- [x] **Aging scheduler** — wire aging/consolidation to background loop (hourly aging, weekly consolidation).
- [x] **Memory API** — structured JSON endpoints for CRUD: list, get, update, delete, search, recency-check. LLM and PWA both use it.

### P1 — Memory UX
- [x] **PWA memory panel** — view facts, edit, delete, promote to core, search, kind badges
- [ ] **PWA memory viewer** — timeline of facts, summary hierarchy, scheduler status, per-fact drill-down
- [ ] **PWA scheduler dashboard** — consolidation runs, logs, next scheduled run, what was processed
- [x] **"Why do you think that?"** — trace fact back to source message (provenance API)
- [x] **Fact rewrite** — `/rewrite` command sends working facts to LLM for cleanup
- [ ] **Inline fact editing** — tap a fact in PWA to edit its text
- [x] **Memory health indicator** — recency report API: healthy/aging/stale/contradictions

### P2 — Pack System v1
- [x] **Move remaining hardcoded prompts** — prompts.py is 92-reference pack-integrated (architecture, not hardcoded)
- [x] **Pack validation CLI** — `uv run python -m pairtner.pack_cli validate packs/default`
- [x] **Pack diff** — `pack_cli diff` compares two pack versions
- [x] **Custom packs** — packs/CUSTOM_PACKS.md comprehensive guide

### P3 — Polish
- [ ] **HTTPS setup** — Tailscale cert or mkcert for PWA install + push notifications. BLOCKED: Chrome requires HTTPS for PWA install and push subscription on non-localhost.
- [x] **LLM module split** — llm.py → llm/ package (base.py, gemini.py, openai_compat.py, __init__.py)
- [x] **User model tier** — /model command, per-user model preference stored in settings
- [x] **Check-in summary** — LLM summarizer wired to check-in flow with goals, trends, conflicts context
- [x] **Digest v2** — rolling pre-computed summaries (daily morning hook)
- [x] **Nudge v2** — agnostic nudges (all shared facts, not just positive), culturally aware, light tone
- [x] **Nudge restart burst fix** — preload state from DB + 5min startup delay + persist delivery timestamp
- [x] **Contract fix** — v2 contract now includes `reply` as REQUIRED first field; mixed text+JSON extraction
- [x] **Persona neutrality** — default neutral (not broken), culturally aware, don't over-therapize
- [x] **LLM error display** — 503/429/404 → user-friendly messages
- [x] **Push debug button** — "🔔 Test notifications" in PWA menu with diagnostics
- [x] **Gemini model audit** — dropped preview models (404), using stable gemini-3.8-flash + gemini-3.6-flash
- [x] **Admin playground** — dry-run extraction/summary, view persona prompt, prefilled test data, edit without touching DB
- [x] **Merged PWA+Dashboard** — single app, role-based auth. Users see their own chat/stats/facts. Admin sees explorer/pipeline.
- [ ] **Summary viewer** — browse current summaries by user→tag→period, with source fact links
- [ ] **User self-service dashboard** — users can see their facts, summaries, tokens, prompt JSON
- [ ] **Multi-relationship dashboard scoping** — relationship selector dropdown, scope all views to selected relationship
- [x] **Merge v1/v2 contracts** — single contract file, task-specific schemas as sections, no v2 flag
- [x] **LLM retry on parse failure** — built: _parse_failed flag + repair prompt + mixed-content extraction
- [x] **Nudge LLM-suggested timing** — LLM suggests best time to nudge based on user patterns; user prefs stored as core facts
- [x] **Nudge frequency user control** — /settings or core fact: "nudge me less", "nudge me daily", "no nudges on weekends"
- [x] **Persona tuning over time** — adaptive tone: warm couple → lighter, strained → more gentle, conflicts → de-escalating. Based on interaction ratios + horsemen + conflict density.
- [x] **/debug and /warnings commands** — inspect knowledge state and quality drops
- [x] **PWA source indicator** — already built: 📱 Telegram, 🌐 PWA badges on messages
- [x] **Offline message queue** — built: localStorage queue + batched flush + ⏳ indicator
- [x] **Separate PWA manifests** — built: /couple/ and /solo/ routes with distinct manifests
- [x] **Topic index** — built: /topics command + get_topic_index in db.py
- [x] **Cost documentation** — built: docs/COST.md with Gemini rate limits

### P4 — Growth
- [ ] **GitHub Pages demo update** — sync demo page with current persona, 2-level visibility, natural language commands, simplified contract. Currently out of date.
- [ ] **Multi-language** — proper i18n framework if needed (not Telugu hardcoded)
- [ ] **Circles** — any relationship (parent-child, siblings, friends)
- [ ] **Postgres option** — for multi-tenant hosting
- [ ] **Voice messages** — transcription → memory pipeline
- [ ] **WhatsApp adapter** — for users without Telegram
- [ ] **PWA per-message 🔒 button** — toggle visibility per message inline
- [x] **Export for therapy notes** — /export therapy generates therapist-friendly markdown + REST endpoint
- [x] **Summary model override** — config.summary_model for heavier model on summaries + POST /api/v1/summaries/trigger
- [x] **Quick + full benchmark** — seed_db.py supports --days N and --quick (30d/90d/365d scenarios)

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


---

## v2 Memory System (192 design decisions in v2/DECISIONS.md)

### Build Queue (in order)
- [x] **Unified summary function** — `create_summary(user_id, period_type, period_id)` for all levels
- [x] **Context window composer** — multi-source: core + top-K working + FTS5 + summaries + proactive
- [x] **Update consolidation** — summaries archive raw facts, weekly → monthly → quarterly cascade
- [ ] **Wire schema v2** — unify table names (atomic rename: update ALL callers + tests in one pass). Attempted and reverted Nov 2026.
- [x] **LLM search endpoint** — two-pass retrieval: LLM returns search_query, engine runs FTS5
- [x] **Proactive poke system** — partner-informed question generation + mixed with camouflage questions. Non-attributable.
- [x] **Chat import** — JSON/WhatsApp/Telegram → batch extract → per-period consolidation
- [x] **Simplified extraction prompt for 4B models** — packs/simple/ with stripped-down contract (reply + memory_ops only), --pack flag in local dev rig
- [x] **Benchmark HTML viewer** — dev/benchmark/viewer.html standalone viewer for benchmark results
- [x] **/debug and /warnings commands** — inspect knowledge state and quality drops
- [x] **Configurable summary intervals** — 9 levels available (daily→yearly), 5 active by default

### Design Docs
- [x] `v2/DECISIONS.md` — 192 numbered decisions from design sessions
- [x] `v2/HOW_IT_WORKS.md` — complete system reference
- [x] `v2/USER_FLOWS.md` — 10 typical user flows
- [x] `v2/contracts/response.yaml` — extraction JSON schemas
- [x] `v2/contracts/scoring.yaml` — scoring configuration
- [x] `v2/prompts/extract.md` — extraction prompt (Gemini + Qwen tested)
- [x] `dev/benchmark/` — model comparison framework + seed DB

### Key Metrics
- Tests: 1544 (core + stress + new features)
- Decisions documented: 340 (v2/DECISIONS.md)
- Benchmark results: Gemini 80%, Qwen3 8B 73%, gemma3 4B 0% (full contract) / 100% (simple)
- Schema: v2 active (visibility 3→2, private tags, 3-stream summaries, rolling summaries)
