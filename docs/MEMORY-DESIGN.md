# pAIrtner core design: engine, prompt packs, memory, context

Status: agreed direction, 2026-11-17. Built in small stable steps; each step is tagged when stable. Details will change as we learn. The design must stay easy to change.

pAIrtner is a framework first. Transports (Telegram, PWA) are only ways in. The parts that matter:
1. How memory is built and kept clean over time.
2. How the LLM gets the data it needs on each call, at a fixed cost.
3. The md files that define the bot's behaviour, packaged so anyone can write and test their own.

## 1. Engine vs prompt pack

| | Engine (Python, stable, changes rarely) | Prompt pack (md + yaml, community-tunable) |
|---|---|---|
| What | Transports, storage, memory store, validation of what the model proposes, privacy rules, flows (approve/edit/discard, cool-down, schedules), buttons, LLM providers, context assembly mechanics | Persona, voices, task prompts, the explanation of the output format, memory policy (what counts as "core"), onboarding gaps, check-in questions, budgets, eval cases |
| Who changes it | Code PRs | Anyone; can be drafted with any model offline and reviewed by a human |
| Can the bot's LLM change it? | No | No. md files are never edited by the running bot |

A pack is a folder with a manifest:

```yaml
# packs/default/pack.yaml
name: default
version: 0.1.0
engine_contract: 1          # which output schema version this pack targets
languages: [en, te]
files:
  persona: persona.md
  contract: contract.md     # explains the JSON output to the model
  memory_policy: memory.md
  voices: voices/
  tasks: tasks/             # chat.md, rewrite.md, mediate.md, onboarding.md, ...
budgets: {core: 250, shared: 150, partner_view: 150, working: 400, episodes: 300}   # words
eval: eval/cases.yaml
```

- `settings.yaml` picks the pack: `prompt_pack: packs/default`.
- The engine validates the pack at startup (files exist, contract version matches) and refuses a broken pack with a clear error.
- Hot reload: a changed md file is re-read on the next call; no restart.
- Every LLM call logs the pack name, version and file hashes, so a bad reply can be traced to the exact prompt text.
- The engine owns the output schema (what it can safely execute). The pack only explains it to the model. If a pack asks for something the engine can't validate, the engine ignores it.

Buttons and flows stay in the engine: they are mechanics (approval, privacy), not tone. Button labels come from i18n strings; packs may override labels later if needed.

## 2. What happens today (from the code)

- Only `persona.md`, `voices/*.md` and `tasks/{nudge,appreciation,summarize,therapy_export}.md` reach the LLM. The real text for chat, rewrite, mediate, digest and check-in, and the whole JSON output contract, are Python strings in `prompts.py`. `tasks/rewrite.md`, `mediate.md`, `digest.md`, `checkin.md`, `articulate.md` and `templates/*` are never loaded.
- The model can only add facts (`new_facts`). No update or status change. Exact-match dedupe. All facts go into every prompt until there are 100. Partner themes pile up unmerged. History is the last 10 messages.

## 3. Memory model

All in SQLite. Raw messages are kept as the source of truth, so any memory can be rebuilt later with a better model or a new pack.

| Kind | Holds | Example | Written by | In the prompt |
|---|---|---|---|---|
| **Core** | Who the person is; relationship basics. Small, durable. | "Introvert, needs quiet after work." "Married 6 years, son (7)." | User explicitly; or model proposes, user confirms | Always (capped) |
| **Working** | Current situations, conflicts, goals in progress. | "Deadline Friday." "Dishes argument (open)." | Model, validated by engine | Ranked, capped |
| **Episodes** | Summaries of past periods, built from raw messages. | "Week of Nov 10: in-laws visit argument, resolved Sunday." | Background summariser | Only if relevant |
| **Partner view** | Merged themes about the partner: who said it, how often, confidence. | "Feels unappreciated for cooking (4x, last Nov 15)." | Model proposes, engine merges | Top few, capped |
| **Shared** | Items a user chose to share; agreed shared goals. | "We agreed: no phones at dinner." | User only | Always (capped) |

**Sharing.** Every core or working item is private to its owner. If the owner taps Share on an item, it is also placed in the partner's prompt (under "your partner has shared") and used by `/mediate`. Never automatic, never by the model.

**Versions, statuses, no deletion by the model.**
- Each memory has a stable id. Every change appends a new version (text, status, relevance, timestamp, which message caused it). The latest version is current; older versions stay for history.
- Statuses: `active`, `improving`, `resolved`, `outdated`. A finished conflict becomes `resolved`; a goal can become `improving`. Status lowers its ranking; it does not remove it.
- The model can never delete. The user can, with `/forget`, and that is a real delete of every version (privacy right).

## 4. Memory operations proposed by the model

The JSON reply carries `memory_ops`. The engine validates and applies them; the user can see and undo.

```json
"memory_ops": [
  {"op": "add",    "kind": "working", "text": "Big deadline Friday"},
  {"op": "revise", "id": 41, "text": "Son is 7, started 2nd grade"},
  {"op": "status", "id": 17, "status": "resolved", "why": "deadline passed"},
  {"op": "propose_core", "text": "Needs quiet time after work"},
  {"op": "pin_core",     "text": "Introvert"}
]
```

Memories are shown to the model with their ids (`[41] Son is 6`) so it can refer to them.

Engine rules the model cannot bypass:
- Ops only touch the current user's own memories; foreign ids are rejected.
- Nothing is ever made shared by the model.
- `pin_core` is accepted only when the user's own message explicitly asks ("remember this about me", "this is how we are"). Then no confirm tap is needed. Otherwise it becomes `propose_core`, which shows a one-tap confirm.
- No delete op exists.
- At most 5 ops per message; near-duplicates of existing memories are merged, not added.

## 5. Context at a fixed cost

The database grows; the prompt does not.

| Block | Cap (words, from the pack) | How it is chosen |
|---|---|---|
| Core (own) | 250 | all; if over the cap, the bot asks the user to merge items (never drops silently) |
| Shared (from partner and couple) | 150 | newest first |
| Partner view | 150 | merged themes by confidence and recency |
| Working | 400 | `active` before `improving` before `resolved`; then relevance to the current message (SQLite full-text search, no LLM call) and recency |
| Episodes | 300 | only when search matches the current message |
| History | last 10 messages | as today |

Whatever doesn't fit is left out of this call, not deleted. Rough size: about 2,000 words of context plus the pack text, independent of how long the couple has used the bot.

**Model-driven search** (`search_memory` tool): off by default. Each tool call resends the whole context, about doubling the tokens for that message, which the free tier can't afford routinely. It may later be allowed for `#deep` only.

## 6. Onboarding as profile gaps

- The pack's `tasks/onboarding.md` lists common gaps (relationship type, time together, kids, what they appreciate, what they'd change) and one open question ("anything you'd like me to know about you or the two of you?").
- Each call gets a short "known / still unknown" block. The model asks at most one gap question, only when it fits; never during a fight or crisis.
- Answers in normal chat fill gaps through `memory_ops`.
- When the user says they're done, onboarding is complete; they can add more any time ("remember this about me").
- `/profile` keeps the explicit question list for people who prefer it.

## 7. Evaluating a pack

- The pack ships `eval/cases.yaml`: message, context, and what a good result must and must not do.
- Runs go through the real engine over the local REST API (the same path as the PWA), with the production model (Gemini Flash). The fake LLM is for flow tests only; small local models are not used to judge quality.
- Results are saved per pack version, so versions can be compared side by side.

## 8. Order of work (each step tagged when stable)

1. **Pack extraction (done).** Every prompt string moved into `packs/default/`; manifest, loader, validation, hot reload and per-call tracing added; assembled prompts are byte-identical to v0.1.0 (`tests/golden/prompts_v0.1.0.json`). Deviations from section 1:
   - Layout is `persona.md`, `contract.md`, `blocks/` (role, not_therapy, tone), `strings.yaml` (all other model-visible words, with checked `{placeholders}`), `voices/`, `tasks/` (nudge, appreciation, summarize, therapy_export). There is no `memory.md` yet.
   - Legacy task and template files the engine does not use (`rewrite`, `mediate`, `digest`, `checkin`, `articulate`, `partner_summary`, `user_context`) moved to `packs/default/unloaded/`: never loaded, excluded from the pack hash.
   - Tracing is both a log line and DB rows (`llm_calls`, `pack_snapshots`), not only logs. Hashes only, no prompt text, no user id.
   - The `/mediate` window label ("the last 7 days") stays in code: it is shared with user-facing i18n.
   - Config keys `persona_file`, `prompts_dir`, `voices_dir` are gone; `prompt_pack` / `PROMPT_PACK` replaces them.
   - The hard-coded persona, voice, summarize and therapy fallbacks are removed: pack validation guarantees those files exist.
2. **Eval runner (done).** `dev/prompt-eval/run_eval.py` runs a pack's `eval/cases.yaml` through the REST API (fake LLM or real Gemini), throttled, and saves results per pack version under `dev/prompt-eval/runs/`. Authoring guide: `docs/PACKS.md`. Deviations from section 7:
   - The runner lives in `dev/prompt-eval/` and drives the engine in-process on a throwaway DB; the rig gained `make_gemini_config` and `run_local.py --llm gemini`.
   - Results: `<pack>/<version>/<UTC time>_<llm>-<model>/` with `summary.md`, `results.json`, `cases/<id>.json`; no verdicts, a person reads the summary.
   - Memory expectations are recorded, not judged: with no `memory_ops` yet, a non-empty `expect.memory` is stored as `not_yet_supported` beside the rows the engine actually wrote.
   - Baseline: `packs/default/eval/baselines/` holds a real Gemini Flash-Lite run of 30 of 31 cases; `md-01-both-sides` is skipped because the heavy models used by `/mediate` were unavailable (404/503). Its README says how to complete it.
3. **Memory store:** ids, versions, statuses, kinds; `memory_ops` validation; ids in prompts; `/whatdoyouknow` grouped by kind with Share, Edit, Forget, Make core.
4. **Context budget** and ranking.
5. **Onboarding as gaps.**
6. **Partner view** merging and attribution check.
7. **Episodes** from raw history.

## Decisions taken
- Core is private by default; per-item Share.
- Explicit "remember this" pins without a confirm.
- Code-side search first; the model's search tool is off by default.
- The model never deletes and never edits md files.
- Every memory change is versioned.
