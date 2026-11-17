# pAIrtner roadmap

pAIrtner is a framework: a small, stable engine plus swappable prompt packs. The core work is memory, context and the packs. Transports (Telegram, PWA) are only ways in.

Design: [`docs/MEMORY-DESIGN.md`](docs/MEMORY-DESIGN.md). Why things are the way they are: [`docs/DECISIONS.md`](docs/DECISIONS.md). System overview: [`ARCHITECTURE.md`](ARCHITECTURE.md).

We build in small stable steps and tag each one. Plans change as we learn.

## Done (v0.1.0)
- Engine: transport-agnostic `BotService`; Telegram, REST + installable PWA, WebSocket and CLI adapters; Telegram and REST in one process.
- Relay flow: draft, Approve / Edit / Discard, cool-down, batch review, delivery; nothing reaches the partner without the sender's approval.
- Privacy: partner data only as themes; the model can never mark anything shared; owner-only Share toggle; allowlist; crisis detection first.
- Memory v0: facts with categories and lifecycle, partner perceptions, goals, conflicts, timeline, digest, check-in, nudges, backups.
- `/mediate` over a time window using shared facts and filtered perceptions.
- LLM providers: Gemini (default) and any OpenAI-compatible endpoint (Ollama, Groq, OpenRouter); per-couple and per-user keys.
- Admin dashboard (stats, read-only SQLite explorer, guarded admin actions).
- Dev tooling: deterministic fake LLM, local rig, scripted two-user scenarios, 1,000+ tests.

## Next, in order
1. **Prompt packs.** Move every prompt string into `packs/default/` with a manifest; pack loader, validation, hot reload, per-call pack logging. No behaviour change.
2. **Pack evaluation.** Run a pack's `eval/cases.yaml` through the REST API on Gemini Flash; save results per pack version; first baseline.
3. **Memory store.** Memory ids, versions and statuses; core / working / partner view / shared; `memory_ops` proposed by the model and validated by the engine; explicit "remember this about me"; `/whatdoyouknow` grouped by kind with Share, Edit, Forget, Make core.
4. **Fixed context budget.** Per-block caps from the pack; ranking by status, relevance and recency.
5. **Onboarding as profile gaps**, with an open question and "that's all" to finish.
6. **Partner view:** merge repeated themes; check who a statement is about.
7. **Episodes:** summaries of past periods from raw history, retrieved when relevant.

## Later
- Model-callable `search_memory` for `#deep` only (token cost).
- Circles: any relationship (parent-child, siblings, friends), groups of more than two.
- Postgres option for multi-tenant hosting.
- More languages; voice messages.

## Known issues
- PWA: messages pushed by the server (review prompt, partner's relay) can reach the browser late or not at all. Telegram is unaffected.
- Partner themes can be attributed to the wrong person by weaker models (addressed in step 6).
