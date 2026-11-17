# pAIrtner core design: memory, context, prompts (DRAFT for review)

Status: draft, 2026-11-17. Nothing here is implemented yet. Sections marked **Decide** need the owner's call.

The core of pAIrtner is three things. Transports (Telegram, PWA) are only ways in.
1. How memory is built: what gets remembered, in what form, and how it changes over time.
2. How the LLM gets the data it needs on each call.
3. The md files that define the bot's behaviour, which should be the only place behaviour is tuned.

## 1. What happens today (from the code, not the docs)

### What one chat call sends to the LLM

| Block | Source | Size today |
|---|---|---|
| Persona | `prompts/persona.md` | ~390 words |
| Role, "not therapy", tone | hardcoded strings in `prompts.py` (`_ROLE`, `_NOT_THERAPY`, `_TONE`) | short |
| JSON output contract | hardcoded `_JSON_FORMAT` in `prompts.py` | ~600 words, 9 output fields |
| Voice and dampening | `prompts/voices/<voice>.md` + learned voice traits + dampening sentence | ~160 words |
| Goals | DB `user_goals` / `shared_goals` | small |
| User's facts | ALL active facts, private then shared (FTS search only above 100 facts) | grows without bound |
| Partner summary | `partner_perceptions` about this user, as `- theme (sentiment)` | grows without bound |
| History | last 10 messages of this user | fixed |
| Current message | the text | |

### Which md files the LLM actually sees

| File | Loaded? |
|---|---|
| `persona.md`, `voices/*.md` | yes |
| `tasks/nudge.md`, `appreciation.md`, `summarize.md`, `therapy_export.md` | yes |
| `tasks/rewrite.md`, `mediate.md`, `digest.md`, `checkin.md`, `articulate.md` | **no** (the real text for these lives in Python strings) |
| `templates/user_context.md`, `templates/partner_summary.md` | **no** |

Editing `rewrite.md` today changes nothing. This has to be fixed before any prompt tuning means anything.

### How memory is written today
- The only write path is `new_facts` in the LLM's JSON reply. The model can add facts. It cannot update, merge, archive, or pin anything.
- Every fact is a one-line string with `category`, `fact_type` (transient/situational/stable), and lifecycle dates.
- Dedupe is exact-match only (case/space/punctuation). "Likes tea" and "Enjoys tea in the morning" are both stored.
- Contradictions are found by a word-pair list (likes/dislikes, loves/hates...).
- The model cannot make anything shared; only the user's Share button can.
- `partner_themes` are stored per message as new rows with no merge, so the same theme piles up, and attribution can be wrong (a weak model filed the user's own feeling as being about the partner).
- Raw messages are kept in `messages` (good: they are the gold), but nothing reads them beyond the last 10.

### Gaps
1. No "core" memory: nothing marks "this is who I am / this is our relationship" as more important than "stressed today".
2. The user can't say "remember this about me" and have it handled as a deliberate, durable memory.
3. The model can't keep memory clean (no update/merge/retire), so memory only grows and gets noisier.
4. Prompt size grows with every fact and theme; quality drops long before the 100-fact search threshold.
5. Behaviour is split between md files and Python strings, and half the md files are dead.

## 2. Proposed memory model

Four kinds of memory per user, plus one per couple. All live in SQLite. Raw messages stay as the source of truth so any memory can be rebuilt later with a better model.

| Tier | What it holds | Example | Who writes it | In every prompt? |
|---|---|---|---|---|
| **Core profile** | Who the person is and the basics of the relationship. Small, curated, durable. | "Introvert, needs quiet after work." "Married 6 years, one son (7)." | User explicitly, or the model proposes and the user confirms | Yes, always (budget ~250 words) |
| **Working memory** | Current situations and recent facts. Ages out. | "Big deadline Friday." "Upset about the dishes this week." | Model (validated by code) | Yes, the active and relevant ones (budget ~400 words) |
| **Episodes** | Summaries of past conversations and events, built from raw messages. | "Week of Nov 10: argument about in-laws visiting, resolved Sunday." | Background summariser | Only when relevant (retrieved) |
| **Partner view** | Merged themes about the partner, with who said it, how often, and confidence. | "Feels unappreciated for cooking (said 4x, last Nov 15)." | Model proposes, code merges | Yes, top few (budget ~150 words) |
| **Shared (per couple)** | What a user chose to share, plus agreed shared goals. | "We agreed: Sunday is phone-free." | User only | Yes |

**Decide:** should the core profile be visible to the partner's prompt? Proposal: no by default. Each core item can be shared like any fact.

## 3. LLM-driven memory operations

Replace `new_facts` with `memory_ops`. The model proposes operations; code validates and applies them; the user can see and undo.

```json
"memory_ops": [
  {"op": "add",     "tier": "working", "text": "Has a big deadline Friday", "kind": "situational"},
  {"op": "update",  "id": 41, "text": "Son is 7, started 2nd grade"},
  {"op": "archive", "id": 17, "reason": "deadline passed"},
  {"op": "propose_core", "text": "Needs quiet time right after work", "why": "user said 'that's just how I am'"},
  {"op": "pin_core", "text": "Introvert", "why": "user said 'remember this about me'"}
]
```

To make `update` and `archive` possible, every memory in the prompt is shown with its id, e.g. `[41] Son is 6`.

**Code-side rules (the model can never bypass these):**
- Ops only touch the current user's own memories; a foreign id is rejected.
- Nothing is ever made shared by the model.
- `pin_core` is only accepted when the user's own message explicitly asks ("remember this", "this is about me", "this is how we are"). Otherwise it is downgraded to `propose_core`.
- `propose_core` shows the user a one-tap confirm ("Should I remember this as part of who you are? [Yes] [No]").
- Updates keep history (old text archived with `supersedes_id`), so nothing is silently lost.
- A per-message cap (e.g. 5 ops) and dedupe against similar existing items.

**Explicit user commands** (work alongside natural language):
- "remember this about me: ..." / `/remember-me ...` → core.
- "this is about us: ..." → core, couple-level, private until shared.
- `/whatdoyouknow` groups by tier, with Share, Edit, Forget, and "make core" buttons.

## 4. How the LLM gets the data it needs

**Context budget per call** (fixed, so quality doesn't drop as memory grows):

| Block | Budget | Selection |
|---|---|---|
| Core profile (own) | ~250 words | all |
| Shared | ~150 words | all, newest first |
| Partner view | ~150 words | top themes by confidence and recency, merged |
| Working memory | ~400 words | active items, ranked by relevance to the message (FTS) and recency |
| Relevant episodes | ~300 words | only if search finds a match |
| History | last 10 messages | as today |

**Retrieval, in two steps:**
1. **Now:** code pre-selects using FTS over facts and episodes with words from the current message, plus recency. No extra LLM call.
2. **Later:** give the model a `search_memory(query)` tool (Gemini function calling) so it can ask for more when it needs it ("what did she say about her mother last month?"). This costs a second call, so only when the first pass isn't enough.

**Decide:** start with step 1 only? (Proposal: yes.)

## 5. md files are the single source of behaviour

All prompt text moves out of Python into md files. Code only assembles blocks and fills data.

```
prompts/
  persona.md            who the bot is (exists)
  contract.md           the JSON output schema and its rules (moved out of prompts.py)
  memory.md             when and how to use memory_ops; what counts as core vs working
  voices/*.md           tone presets (exist)
  tasks/chat.md         normal conversation (new)
  tasks/rewrite.md      relay drafts (exists, currently unused)
  tasks/mediate.md      third-person perspective (exists, currently unused)
  tasks/onboarding.md   profile gaps / open questions (new, see section 6)
  tasks/digest.md, checkin.md, nudge.md, ... (exist)
  templates/context.md  the order and headings of the context blocks
```

- Hot reload: re-read a file when its modification time changes. No restart.
- Every prompt build records which md files and versions it used, so a bad reply can be traced to a prompt version.

## 6. Onboarding without a questionnaire feel

- `tasks/onboarding.md` lists common gaps: relationship type, how long together, kids, what they appreciate, what they'd like to change, plus an open "anything you'd like me to know about you or the two of you?"
- Each call gets a short "Known so far / Still unknown" block for this user.
- The model asks at most one gap question, and only when it fits the conversation. It never asks during a fight or a crisis.
- Answers given in normal chat ("my son's school...") fill the gap through `memory_ops`.
- When the basics are known, it asks the open question once. If the user says they're done ("that's all"), onboarding is marked complete. The user can always add more later ("remember this about me").
- The explicit question list stays available as `/profile` for people who prefer it.

## 7. How we judge changes (prompt evaluation)

- A fixed set of test cases (`dev/prompt-eval/cases.yaml`): message, context, and what a good reply must and must not do.
- Run against Gemini (the production model), never tuned on the local 8B model.
- Each run is saved with the md file versions, so two prompt versions can be compared side by side.
- Owner reviews; we change md files; rerun.

## 8. Order of work

1. Move every prompt string into md files, delete dead text, add hot reload. Behaviour should stay the same; verified with the eval set.
2. Build the eval set and the runner; get a baseline on Gemini.
3. Memory tiers and `memory_ops` (schema, validation, prompt ids, `/whatdoyouknow` by tier).
4. Context budget and retrieval step 1.
5. Onboarding as profile gaps.
6. Partner view merging and attribution check (fixes the mixed-up themes).
7. Episodes (summaries of raw history) and, later, the `search_memory` tool.

## Open questions for the owner
1. Core profile private by default, shareable per item? (Proposal: yes.)
2. Should `pin_core` need a confirm tap even when the user explicitly says "remember this"? (Proposal: no confirm when explicit.)
3. Retrieval: code pre-selection first, model tool later? (Proposal: yes.)
4. Word budgets above: fine as a start?
