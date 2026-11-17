# Decisions

Why pAIrtner is built the way it is. Newest first. Each entry: the decision, why, and what we rejected.

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
