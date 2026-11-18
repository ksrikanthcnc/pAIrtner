# v2 Design Decisions — Conversation Log

All decisions from the design discussion session (2026-11-18/19).
This is the authoritative reference. The other v2/*.md files should be updated to match.

---

## Terminology

| Term | Definition |
|---|---|
| **Fact** | A single piece of knowledge about a user. One sentence, one idea. E.g. "She's 34", "Hates mornings", "Stressed about interview Friday." |
| **Knowledge** | The curated collection of facts for a user. Stored in the `knowledge` table. Managed by the LLM + engine, never by user input directly. |
| **Raw log** | The `messages` table. Every message (user + bot) appended, never modified. Source of truth for rebuilds. |
| **Core fact** | Identity-level, durable fact. Always included in LLM prompt. No time decay. CAN be revised ("married 6y" → "married 7y") but never auto-archived. |
| **Working fact** | Current situation, temporary. Decays over time (21-day halflife). Gets archived when resolved/outdated. |
| **Episode** | A compressed summary of older facts/events. Created by weekly consolidation job. Replaces the working facts it summarizes. |
| **Citation** | When a fact is included in the LLM's prompt window, that's a citation. `citation_count++` on each inclusion. Creates feedback loop: useful facts score higher → get cited more → stay relevant. |
| **Scoring** | Formula that ranks facts for prompt inclusion: `kind_weight × decay(age) × state_weight × citation_boost`. Precomputed on every write and citation event. Context builder does `ORDER BY relevance_score DESC LIMIT N`. |
| **Memory op** | A structured JSON instruction from the LLM to modify knowledge: add, revise, state change, promote. Validated by the engine before applying. |
| **Persona** | An MD file that defines the bot's identity and tone for a specific relationship type (couple, parent-child, friends). |
| **Prompt** | An MD file with task-specific LLM instructions (chat, mediate, extract, rewrite). Shared across all relationship types. |
| **Contract** | A YAML file defining what JSON the LLM sends/receives. The engine validates against it. |

---

## Core Architecture

1. **Two core tables: `messages` (raw log) + `knowledge` (LLM-curated facts).** Everything else derives from these. Down from 27 tables in v1.
2. **Support tables kept:** `users`, `user_profiles`, `couple_config`, `auth_tokens`, `usage`, `pending` (relay). ~8 total.
3. **Raw messages include bot replies.** Both user and assistant messages logged. Needed for admin chat view and knowledge worker context.
4. **Knowledge table replaces `facts` table.** Same concept, evolved columns. No separate profile table — user profile data (age, DOB, etc.) stored as core facts.

## Knowledge Table Schema

5. **`source_ids` column is JSON array** (not single int). Points to raw message IDs only, never to other knowledge IDs. Summaries source from multiple messages: `[1,2,5,12]`.
6. **No recursive summary chains.** Summaries always point to raw message IDs. If a summary needs rebuilding, re-read the raw messages. Flat, not chained.
7. **`metadata` JSON column** for extensibility. Any new LLM output field (mood, confidence, etc.) stored here until promoted to a dedicated column. No migration needed for new fields.
8. **`model` column** tracks which model created the knowledge entry. Useful for debugging and future model comparison.
9. **Kinds:** `core` (identity, permanent, always in prompt), `working` (current situations, decays), `episode` (compressed summaries). All weekly summaries become episodes.
10. **Core facts CAN be revised** (e.g. "married 6 years" → "married 7 years"). Core means "always in prompt + no decay", not "immutable."

## Scoring System

11. **Formula:** `score = kind_weight × decay(age) × state_weight × citation_boost`
    - `kind_weight`: core=1.0, episode=0.7, working=0.5, partner=0.4
    - `decay(age)`: `e^(-age_days / halflife)` — core=∞ (no decay), episode=180d, working=21d
    - `state_weight`: active=1.0, improving=0.9, resolved=0.3, outdated=0.1
    - `citation_boost`: `min(1.0 + 0.1 × citation_count, 1.5)` — capped at 1.5x
    - Core facts always score 1.0 (kind_weight=1.0 × no decay × active=1.0).
    - A 3-month-old working fact with no citations: 0.5 × 0.003 × 1.0 × 1.0 ≈ **0.001** (effectively invisible).
    - An episode cited 5 times, 2 months old: 0.7 × 0.65 × 1.0 × 1.5 ≈ **0.68** (still prominent).
12. **Weights are developer knobs, not user-facing.** Start with v1 values. Tune based on testing. The ordering matters more than exact numbers (core > episode > working).
13. **Citation = fact was included in LLM prompt.** Automatic. Each time the context builder picks a fact for the prompt window, `citation_count++` and `last_cited_at` updated. Creates natural feedback loop: useful facts stay high, stale facts drop.
14. **Score is precomputed** on writes and citation events. Context builder just does `ORDER BY relevance_score DESC LIMIT N`. One indexed query.
15. **v1 scoring is good enough to start.** Can add `intensity` (user-set weight) later. Don't over-tune before real usage data.
16. **All scoring knobs live in `contracts/scoring.yaml`** with descriptions. Single file, easy to tune. Engine reads at startup.

## LLM Strategy

17. **Gemini Flash Lite for everything initially.** 500 RPD is enough for a couple (30-50 messages/day + 5-10 extraction calls). Switchable via config to any OpenAI-compatible endpoint.
18. **Start with accuracy on Gemini, then shrink to local.** Gemini first = isolate the prompt quality. Then test same prompts on smaller models.
19. **Qwen3:8B test results:** Valid JSON 3/3 with thinking OFF. But misses contradictions (~66%) and fact updates (~66%). Fine for simple extraction, not reliable for reasoning tasks like dedup/contradiction. Confirmed: Gemini for reasoning, local for simple extraction (future optimization).
20. **Bigger model (Flash, not Lite) for weekly summaries.** More reasoning needed for consolidation. Mentioned to users that summary uses more quota.
21. **Single LLM call per message:** returns both `reply` (sent to user) AND `memory_ops` (applied to knowledge). Not two separate calls.
22. **No fast filter for dedup.** Send candidates directly to LLM. At <500 facts per user, this is feasible. Add fast filter later if needed (trivial: just a WHERE clause before the LLM call).
23. **Dedup/contradictions handled naturally by LLM.** Include existing facts in prompt + one instruction line: "If you see duplicates or contradictions, include state_changes to resolve them." No special system.

## Context Window (What LLM Sees)

24. **Knowledge:** Core facts (always) + top-K working by score + top-K episodes + FTS5 matches for current message. ~1200-1600 words total.
25. **Recent messages:** Last 10-20 raw messages (both user and bot). Gives conversational coherence.
26. **Time-aware vs blind message cutoff:** Start with blind (last 20). Add session boundaries later if cross-session bleeding is a problem. **TODO: revisit after testing.**
27. **Casual messages ("what's up"):** Same knowledge context. Trust the LLM to respond casually without referencing heavy facts. No different scoring mode needed. Casual messages don't inflate the knowledge table — the LLM simply returns empty `memory_ops` when there's nothing to learn.
28. **Context injection for time-sensitive facts:** Date-based facts (birthdays, deadlines, goals) are injected into the context when their date approaches, REGARDLESS of their score. This is rule-based, not score-based: a background job marks approaching-date facts with a `context_inject: true` flag, and the context builder includes them unconditionally. Example: "Partner's birthday in 3 days" appears in context even if its score is low. This also means the LLM might naturally weave it into conversation ("By the way, isn't your anniversary coming up?") without being explicitly asked.

## Summary & Consolidation

29. **Weekly summary job:** Reads raw messages + existing knowledge → produces episode entries. Source facts can be archived after episode creation.
30. **Cascading summaries (1d, 3d, 7d... rolling):** PARKED. Interesting idea for keeping old important things alive in compressed form. Currently handled by scoring (cited things stay high regardless of age). If scoring isn't enough, this is the upgrade path. Architecture supports it (just another background job writing episodes). **NOTE: Keep pluggable — should be easy to add later.**
31. **Episodes point to raw message IDs via `source_ids`.** Can be rebuilt from raw if extraction logic changes.

## Extensibility

32. **Relationship types = persona + prompt override files.**

    Structure:
    ```
    personas/
    ├── _base.md                # shared identity rules (tone, safety, memory policy)
    ├── couple.md               # imports _base.md + couple-specific tone/rules
    ├── parent_child.md         # imports _base.md + parent-child specifics
    └── friends.md              # imports _base.md + friend specifics

    prompts/
    ├── _base_chat.md           # shared chat instructions
    ├── _base_mediate.md        # shared mediation instructions
    ├── couple/
    │   ├── chat.md             # couple chat (imports _base_chat + couple overrides)
    │   └── mediate.md          # couple mediation (romantic conflict style)
    ├── parent_child/
    │   ├── chat.md             # parent-child chat (respect authority dynamics)
    │   └── mediate.md          # parent-child mediation (generational gaps)
    └── friends/
        ├── chat.md
        └── mediate.md
    ```

    **How it works:**
    - `_base.md` files define shared rules (safety, memory policy, JSON contract).
    - Relationship-specific files import the base and add/override rules.
    - Code loads `personas/{type}.md` + `prompts/{type}/chat.md` at runtime.
    - The memory engine, scoring, relay — all identical regardless of type.
    - Adding a new relationship type = create a persona file + prompt folder. Copy an existing one, tweak the tone.

    **Example difference — couple vs parent-child mediation:**
    - `couple/mediate.md`: "Both partners are equals. Never take sides. Focus on emotional needs and compromise."
    - `parent_child/mediate.md`: "Respect the generational difference. Help the parent understand modern perspectives. Help the child understand the parent's concerns come from care. Never undermine parental authority."

    **Import/hierarchy:** Simple string concatenation at load time. `couple/chat.md` starts with `<!-- import: ../_base_chat.md -->`. The loader reads the base, appends the override. Not a framework — just file concatenation.

33. **Changing an MD file immediately affects all future LLM calls.** For rewrites/re-extraction, run a rebuild job with the updated prompts.

34. **Group support:** Same engine, same knowledge table. Query all user_ids in the group. Persona file says "you're mediating a group." Relay becomes "draft for the group."

35. **Contract YAML defines what LLM sends/receives.** Not a code generator. Schema changes = SQL migrations (hand-written, 3 lines). New LLM output fields → `metadata` JSON column first, promote to real column when proven valuable.

## Features

36. **DOB/date storage:** Store dates in ISO format as core facts: `"DOB: 1992-03-15"`, `"Anniversary: 2020-06-15"`, `"Goal deadline: 2027-01-15"`. Background job scans core facts daily for date patterns, injects approaching dates into context (see decision 28). This enables:
    - LLM naturally mentioning upcoming events in conversation
    - Partner being aware that the other might be thinking about an event
    - Goal deadline awareness without explicit reminder commands

37. **Archiving flow:** `active → outdated → archived`. Archived = `archived_at` set, excluded from all queries except admin. Triggered by: aging job (uncited 30d), LLM state_change, user delete, or weekly summary.

38. **LLM can dig into sources:** When unsure about a fact, LLM requests raw messages via `source_ids`. Engine fetches and includes in follow-up prompt. Tool/function call pattern.

## Configuration

39. **All tunable knobs in `contracts/scoring.yaml`** with descriptions:
    ```yaml
    # All scoring and context parameters. Engine reads at startup.
    # Developer knobs — not user-facing.

    scoring:
      kind_weight:
        core: 1.0        # identity facts, always in prompt
        episode: 0.7     # compressed summaries of past periods
        working: 0.5     # current situations, temporary
        partner: 0.4     # unverified impressions about partner
      halflife_days:
        core: null        # infinite — no decay
        episode: 180      # slow fade over 6 months
        working: 21       # fast fade over 3 weeks
        partner: 30       # moderate fade
      state_weight:
        active: 1.0
        improving: 0.9
        resolved: 0.3
        outdated: 0.1
      citation_boost_per: 0.1    # +0.1 per citation
      citation_boost_max: 1.5    # cap at 1.5x
      score_cap: 1.0             # max score

    context:
      budget_words:
        core: 300
        working: 400
        episodes: 200
        fts_matches: 200
        recent_messages: 500
        total: 1600
      recent_message_count: 20   # last N messages included
      fts_match_limit: 5         # top N FTS5 results per message

    aging:
      uncited_outdated_days: 30  # mark as outdated after N days without citation
      outdated_archive_days: 14  # archive N days after becoming outdated
      transient_archive_days: 14 # auto-archive transient facts after N days

    consolidation:
      interval_days: 7           # run weekly
      min_facts_for_episode: 3   # need at least N facts to consolidate
      max_facts_per_episode: 10  # summarize up to N facts into one episode

    date_injection:
      lookahead_days: 7          # inject date facts N days before they occur
      scan_interval_hours: 24    # how often to scan for approaching dates
    ```

## Approach

40. **Enhance v1, not full rewrite.** Keep adapters, PWA, relay, tests. Rewrite only: knowledge table + extraction prompt + context builder. ~500 lines new replacing ~2000 lines of over-engineered storage modules.
41. **No v1/v2 engine switch.** Tag v1 (done: `v1.0`), move forward on master. No dual maintenance.
42. **Admin features preserved:** Merged chat view (both users + LLM output), read-only SQLite explorer, runtime config editor.

## Testing

43. **Qwen3:8B local test completed.** Results in `tmp-kiro/qwen-json-test/`. Valid JSON with thinking OFF, unreliable reasoning.
44. **Use `duo_keep` DB for real conversation data.** Read-only reference for extraction prompt testing. Don't overwrite raw messages.
45. **Next step: extraction prompt → test on Gemini Flash Lite → establish accuracy baseline.**

## Proactive Context Injection (decision 28, broadened)

46. **`context_inject` is a general mechanism, not just dates.** Any fact the LLM should see regardless of its score. Use cases found so far:
    - Approaching dates (birthdays, deadlines, anniversaries)
    - Unresolved conflicts older than N days
    - Goals with approaching deadlines
    - Partner inactivity (hasn't messaged in N days — context, not notification)
    - Build the injection mechanism once, add scan rules incrementally as we find more use cases.

47. **Scheduled proactive messages are intelligent, not blind cron.** Before firing a check-in or follow-up, the engine checks recent messages. If the topic was already discussed organically, skip. Implementation: the scheduled job includes recent messages in its LLM call; the LLM decides whether to send or skip. Not a dumb timer.

48. **LLM-set timers (future, parked for MVP).**
    - LLM proposes: `"timer": {"check_in": "3 days", "topic": "how did the conversation with mom go?"}`
    - Engine stores in a `timers` table, fires as a proactive message when due.
    - **Cancellation/reset:** If the user addresses the topic before the timer fires (e.g. LLM set a "check back about interview" timer but user says "interview went great" the next day), the timer should be cancelled. Two approaches:
      - **(a) Include active timers in context:** Add pending timers to the LLM prompt. The LLM can then return `"cancel_timer": <id>` when the topic is resolved naturally. Requires increased context budget for timers.
      - **(b) Timer scan at fire time:** When a timer is about to fire, the engine checks if recent messages already cover the topic (keyword/FTS match). If yes, auto-cancel without firing.
    - Likely: both. (a) for explicit cancellation, (b) as a safety net.
    - **NOTE:** This is a v2.1 feature. For MVP, engine-driven schedules only. But the `timers` table and the cancellation design should be noted so we don't paint ourselves into a corner.

## Build Order

1. Write extraction contract + prompt (30 min)
2. Test on Gemini Flash Lite with real conversation data (30 min)
3. Knowledge table + Memory API v2 (1 session)
4. Wire into existing bot (1 session)
5. Ship. Iterate based on real usage.
