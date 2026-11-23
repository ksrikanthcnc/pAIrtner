# Decisions

Why pAIrtner is built the way it is. Newest first. Each entry: the decision, why, and what we rejected.

## 2026-11-18 — Legacy prompt files live in `packs/default/unloaded/`
- **Decision:** md files the engine no longer uses are kept inside the pack under `unloaded/`, with a README saying they are never loaded. The manifest names the folder, the loader ignores it, the pack hash excludes it, and the validator rejects any `files:` entry pointing into it.
- **Why:** the live `digest`, `check-in` and `mediate` text is new pack text, so a sibling legacy `tasks/` folder would hold two different files with the same name. A separate folder keeps one source of truth.
- **Rejected:** deleting them (loses tuning reference); keeping them next to the live files.

## 2026-11-18 — Every LLM call is traced in both the log and the database
- **Decision:** each call logs `llm_call task= model= pack=name@version pack_hash= prompt_sha=` and writes `llm_calls`; `pack_snapshots` holds the sha256 of every pack file once per distinct pack hash. A `TracingLLM` wrapper does this, reading the snapshot taken when the prompt was assembled.
- **Why:** a bad reply must be traceable to the exact prompt text, including after a hot reload. Logs rotate; the DB survives and can be shown in the dashboard. Hashes only: no prompt text and no user id, so the dashboard can show these tables safely.
- **Rejected:** log only (lost on rotation); storing prompts (privacy).

## 2026-11-18 — Words in the pack, structure in code
- **Decision:** every word the model reads moves to the pack. Code keeps block order, separators, markdown markers (`## `, `- `), numeric thresholds, output parsing, privacy filters, and the user-facing fallback reply and `/mediate` window label.
- **Why:** privacy rules (which data appears in whose prompt) must not be editable by pack authors, while wording must be. The refactor is verified byte-identical to v0.1.0 against a golden snapshot.
- **Rejected:** templating the whole assembly in the pack (pack authors could break privacy).

## 2026-11-17 — Framework: engine + prompt packs
- **Decision:** split the project into an engine (code: transports, storage, memory validation, privacy, flows) and prompt packs (md + yaml: everything the model reads, plus eval cases). Packs are selected in `settings.yaml`.
- **Why:** bot features will change slowly; the prompts and memory policy are where research and community work happen. Anyone should be able to write a pack, including with another model, and prove it with the same eval cases.
- **Rejected:** letting the running bot's LLM edit md files (unreviewed self-modification); YAML-defined commands (abstraction cost without benefit at this size).

## 2026-11-17 — Memory: kinds, versions, statuses; the model never deletes
- **Decision:** memories are core / working / episodes / partner view / shared. Every change appends a version. The model changes status (`resolved`, `improving`, `outdated`) instead of deleting. Only the user can delete (`/forget`), and that removes all versions.
- **Why:** history is the asset; models make mistakes; a resolved conflict is still useful context. Users must still be able to truly delete their data.
- **Rejected:** model-driven deletes; flat ever-growing fact lists.

## 2026-11-17 — Fixed context budget; search by code first
- **Decision:** each prompt block has a cap; items are ranked by status, relevance (SQLite full-text search) and recency. A model-callable search tool is off by default.
- **Why:** prompt cost and quality must not degrade as a couple uses the bot for months. A tool call resends the whole context and roughly doubles tokens; the free tier can't afford that per message.

## 2026-11-17 — Sharing is always a user action
- **Decision:** everything is private to its owner. The owner can share individual items; shared items appear in the partner's prompt and in `/mediate`. The model can never mark anything shared (the engine forces private).
- **Why:** found in testing: the only path to "shared" was the model's own label. A misbehaving model could publish private notes.

## 2026-11-17 — Prompt quality is judged on the production model
- **Decision:** flow tests use a deterministic fake LLM (`reply = UPPER(input)`); quality evaluation uses Gemini Flash through the real engine. Small local models are not used to tune prompts.
- **Why:** an 8B local model flips meanings and breaks JSON; tuning to it would overfit prompts to its failures.

## 2026-11-16 — Transports are thin; the PWA is a full alternative to Telegram
- **Decision:** one transport-agnostic `BotService`; adapters for Telegram, REST (+ installable PWA), WebSocket, CLI. Telegram and REST can run in one process. Telegram users and PWA users can be paired.
- **Why:** Telegram is not end-to-end encrypted for bots; some users will prefer a self-hosted web app on their own network.

## 2026-11-16 — SQLite; raw messages kept
- **Decision:** SQLite with raw messages stored alongside derived memory.
- **Why:** zero infrastructure, file backup, built-in full-text search; raw history lets any future model or pack rebuild memory. Postgres only if multi-tenant scale needs it.

## 2026-11-16 — Partner sees themes, never raw text
- **Decision:** what one partner says about the other reaches the other's prompt only as merged themes, never verbatim.
- **Why:** good mediation needs both perspectives; raw text in the other prompt would be extractable by prompt injection.

## 2026-11-16 — Approval before anything reaches the partner
- **Decision:** every relay is shown to the sender (Approve / Edit / Discard), then waits a cool-down; queued relays are merged and reviewed before delivery (configurable).
- **Why:** the bot must never send something the sender didn't explicitly approve; the cool-down prevents rapid-fire escalation.

## 2026-11-16 — Gemini free tier first, provider pluggable
- **Decision:** Gemini Flash-Lite by default; any OpenAI-compatible endpoint (Ollama, Groq, OpenRouter) via config; per-couple and per-user API keys.
- **Why:** zero cost for a couple; no lock-in.

## 2026-11-16 — Not therapy; crisis first
- **Decision:** crisis language skips all processing and returns helplines (India first). The bot says it is not a therapist.
- **Why:** safety over every other feature.

## 2026-11-17 — Task-scoped output schemas
- **Decision:** each LLM task gets only the fields it needs. Per-message: reply, memory_ops, relay_draft, detected_mood, articulation_note. Patterns/horsemen/voice_traits move to a periodic background analysis (every N messages, heavy model).
- **Why:** flash-lite does 3 things well per call, not 9. Simpler schema → better adherence. Patterns and horsemen are more accurate from a batch of messages than from one. Background analysis uses the heavy model and runs 10x less often → same quality, lower cost.
- **Rejected:** keeping all 9 fields per message (quality suffers on light models); separate LLM calls per field (wasteful); pure-classifier mood detection (misreads sarcasm and cultural context).

## 2026-11-17 — Packs are the only plugin interface
- **Decision:** a pack is a folder of markdown + YAML. No Python in packs. The engine adapts to the pack's configuration implicitly (budgets change context assembly, memory policy changes what's core, task prompts change output). The engine's task set is fixed — packs can't add commands or flows.
- **Why:** anyone should be able to write a pack without knowing Python. The engine is stable infrastructure; packs are the research surface. Keeping packs to markdown + YAML makes them safe to share, easy to review, and impossible to break the engine with.
- **Rejected:** Python plugin API (security, complexity); YAML-defined commands (abstraction cost); registry/package manager (overkill for folder distribution).

## 2026-11-17 — Three-tier evaluation
- **Decision:** fake LLM for plumbing tests (deterministic, free), Ollama for prompt iteration (local, free, fast), Gemini Flash for production validation (real API, token cost). Eval cases in YAML, results saved per pack version + model.
- **Why:** tuning prompts against a cloud API wastes tokens on iterations that mostly fail. Local models give fast feedback. The production model is the final gate, not the development loop.
- **Rejected:** single-tier eval (either too slow or too cheap to be useful); model-as-judge for all checks (expensive and unnecessary for deterministic assertions like privacy).

## 2026-11-17 — pin_core requires explicit user command
- **Decision:** `pin_core` (add directly to core, bypassing confirm) is accepted only when the engine detects a command/tag (`/remember`, `#remember`) or clear directive phrasing. Detection is keyword-based in the engine, not LLM-based.
- **Why:** the LLM's judgment on "is this explicit enough?" is unreliable. A keyword check is deterministic. The model can always `propose_core` (with user confirm) as the safe fallback.
- **Rejected:** LLM decides if the request is explicit (too ambiguous); all core additions require confirm tap (friction for explicit requests like "remember this about me").

## 2026-11-17 — First-person vs third-person mode (planned)
- **Decision:** add a `/mode` setting with two values: `mediator` (default, third-person — the AI is a visible intermediary) and `voice` (first-person — the AI writes as the user, in their voice). Affects relay drafting and bot framing.
- **Why:** first-person relays read more naturally to the receiver ("I've been feeling..." vs "Your partner feels..."). Some couples prefer the bot invisible; others want the mediator framing. Making it a mode lets both work.
- **Status:** planned, not yet implemented. Needs changes to persona.md, contract.md relay section, and relay drafting logic.

## 2026-11-23 — Dampening is progressive training toward healthy communication (bidirectional)

- **Decision (clarification):** Dampening is a progressive teaching methodology that nudges users toward healthy, assertive communication — the CENTER, not one end. Direction depends on the user's current pattern:
  - Aggressive/abusive user → dampen toward gentler, more constructive expression
  - Passive/avoidant user → dampen toward more assertive, direct expression
  - Already healthy → minimal dampening, maintain current style
- **Scope:** Dampening belongs in ALL interactions (not relay-only), because teaching happens in regular conversation, not just message delivery. The relay inherits the user's current effective style.
- **The knob (0.0-1.0):** Controls rate of change (acceleration, not velocity). High value = change slowly and carefully. Low value = push faster. The DIRECTION is determined by the user's current communication pattern, not the knob value.
- **Status:** Currently implemented as a static "softness level" (velocity model). Two future changes: (1) make it bidirectional (detect if user needs softening OR assertiveness), (2) make it acceleration-based (progressive, with maturity tracking).
- **Rejected:** relay-only scoping (teaching happens in all interactions); removing dampening entirely (progressive communication training is a core value proposition).

## 2026-11-23 — Episodes are legacy; rolling summaries replace them

- **Decision:** The `episode` fact kind was the original consolidation mechanism (group 5-10 related working facts into a narrative). Rolling summaries (daily/weekly/monthly/quarterly) now serve the same purpose with a cleaner time-based model. Episodes should not be created by new code.
- **Why:** Two consolidation systems doing similar things adds complexity. Rolling summaries are time-based and predictable; episodes are ad-hoc and overlap with weekly summaries.
- **Status:** Episode creation code still exists in `consolidation.py`. New features should use the rolling summary path. Removal of the episode system is a future cleanup.

## 2026-11-23 — Working facts ARE archived when their summary is created (confirmed)

- **Decision (confirmed from code):** Period summaries (`create_summary`) pass `archive=True` to `_store()`, which sets `archived_at` on all source working facts. The context composer filters `archived_at IS NULL`, so archived facts DO leave the prompt once summarized.
- **How it works:** Daily maintenance runs `run_periodic_summaries()` → for each completed period, gathers working facts in that window → LLM summarizes → stores as kind='daily'/'weekly'/etc → archives source facts. Then `run_rolling_summaries()` reads remaining unarchived working facts + completed summaries → creates a rolling preview (kind='rolling', replaced in-place, NO archival).
- **Prompt contains:** core facts + unarchived working facts (today's + unprocessed) + rolling summaries (gap filler) + period summaries (the real history).
- **Corrects earlier note:** An earlier entry said "working facts should archive when summary is created — not yet implemented." This was wrong; it IS implemented. The confusion arose from testing on a fresh DB where the maintenance loop hadn't run.

## 2026-11-23 — Fact-saved notices removed from regular chat

- **Decision:** Regular chat messages no longer append "✅ Saved 2 new fact(s)" after the LLM's reply. Facts are still extracted and stored silently. The `#keep` command still shows what was saved (explicit request).
- **Why:** The appended notice breaks conversational flow. The LLM gives a warm empathetic response, then a mechanical data notice. The LLM's reply IS the conversation; metadata belongs in /debug.

## 2026-11-23 — Simplified onboarding: no /skip, no forced password

- **Decision:** Onboarding completes on ANY first message after pairing. Greetings ("hi", "hey") complete instantly with no extraction. Messages with facts are auto-extracted by the LLM. No /skip advertised (still works silently). Password mentioned in the welcome message, not forced as a separate step.
- **Why:** /skip was an unnecessary friction point. The LLM already handles extraction from free-text. Making the user type a specific command to bypass a step they didn't ask for is bad UX.
